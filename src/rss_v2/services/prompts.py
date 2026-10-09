"""提示词库用例：版本管理、编译校验与试跑。

试跑与真实任务共用候选调度、Key 冷却和调用审计，但只写 llm_calls，不写译文或
加工结果——它是"看提示词会得到什么"，不是正式产出。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, cast

from rss_v2.domain import (
    DomainError,
    EnrichmentResult,
    Prompt,
    PromptSample,
    PromptTest,
    Provider,
    ProviderKey,
    TaskType,
    TranslationResult,
)
from rss_v2.domain.prompts import CompiledPrompt, compile_prompt, prompt_values, spec_for
from rss_v2.domain.values import mask_secret, new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import PromptRepository
from rss_v2.services.llm_failover import LLMFailover, input_hash

#: 提示词业务键：会拼进审计版本串与任务幂等键，因此限制为小写 slug。
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@dataclass(slots=True)
class PromptService:
    """提示词版本的读写、编译与试跑。"""

    prompts: PromptRepository  # 提示词版本
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与调用审计
    cooldown_seconds: int  # 临时错误冷却秒数

    def list(self, task_kind: str | None = None) -> list[Prompt]:
        """列出提示词版本；按任务类型过滤时先校验类型。"""

        if task_kind is not None:
            spec_for(task_kind)
        return self.prompts.list(task_kind)

    def get(self, prompt_id: str) -> Prompt:
        prompt = self.prompts.get(prompt_id)
        if prompt is None:
            raise DomainError("prompt_not_found", "提示词不存在")
        return prompt

    def compile(
        self,
        task_kind: str,
        system_template: str,
        user_template: str,
        sample: PromptSample | None = None,
    ) -> CompiledPrompt:
        """编译校验与预览渲染；不写库。"""

        values = prompt_values(sample.title, sample.summary, sample.content) if sample else None
        return compile_prompt(task_kind, system_template, user_template, values)

    def create_version(
        self,
        task_kind: str,
        prompt_key: str,
        name: str,
        system_template: str,
        user_template: str,
        note: str = "",
    ) -> Prompt:
        """新建一个提示词版本；编译不通过直接拒绝。"""

        spec_for(task_kind)
        if not KEY_PATTERN.match(prompt_key):
            raise DomainError("prompt_key_invalid", "提示词标识只能用小写字母、数字和短横线")
        compiled = compile_prompt(task_kind, system_template, user_template)
        if not compiled.ok:
            detail = "；".join(issue.message for issue in compiled.errors)
            raise DomainError("prompt_invalid", f"提示词编译未通过：{detail}")
        timestamp = now()
        return self.prompts.create(
            Prompt(
                id=new_id(),
                task_kind=task_kind,
                prompt_key=prompt_key,
                version=self.prompts.next_version(prompt_key),
                name=name.strip() or prompt_key,
                status="draft",
                system_template=system_template,
                user_template=user_template,
                note=note.strip(),
                created_at=timestamp,
                updated_at=timestamp,
            )
        )

    def activate(self, prompt_id: str) -> Prompt:
        """启用某个版本；同任务类型的其它启用版本自动归档。"""

        prompt = self.get(prompt_id)
        self.prompts.archive_kind(prompt.task_kind, prompt.id)
        return self.prompts.set_status(prompt.id, "active")

    def archive(self, prompt_id: str) -> Prompt:
        """归档某个版本；已排队的任务仍按快照执行。"""

        return self.prompts.set_status(self.get(prompt_id).id, "archived")

    def test(self, prompt_id: str, sample: PromptSample, model: str | None = None) -> PromptTest:
        """用样例输入真实调用一次模型，返回结构化输出或失败原因。"""

        prompt = self.get(prompt_id)
        values = prompt_values(sample.title, sample.summary, sample.content)
        compiled = compile_prompt(
            prompt.task_kind, prompt.system_template, prompt.user_template, values
        )
        if not compiled.ok:
            detail = "；".join(issue.message for issue in compiled.errors)
            raise DomainError("prompt_invalid", f"提示词编译未通过：{detail}")
        payload = PromptPayload(system=compiled.system, user=compiled.user)
        prompt_version = f"{prompt.version_string}+prompt-test"
        digest = input_hash(payload.system, payload.user)

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[object, dict[str, int], int]:
            return self._invoke(
                prompt.task_kind, provider, model_name, secret, payload, prompt_version
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: object,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> PromptTest:
            return PromptTest(
                ok=True,
                model=model_name,
                key_masked=mask_secret(key.secret),
                latency_ms=duration_ms,
                total_tokens=int(tokens.get("total_tokens", 0)),
                system=payload.system,
                user=payload.user,
                output=_output(result),
                error_code=None,
                error_message=None,
            )

        try:
            return self.failover.execute(
                None, digest, prompt_version, self.cooldown_seconds, invoke, persist
            )
        except DomainError as exc:
            candidates = self.failover.candidates(None)
            return PromptTest(
                ok=False,
                model=model or (candidates[0][1] if candidates else ""),
                key_masked="",
                latency_ms=0,
                total_tokens=0,
                system=payload.system,
                user=payload.user,
                output={},
                error_code=exc.code,
                error_message=exc.message,
            )

    def _invoke(
        self,
        task_kind: str,
        provider: Provider,
        model_name: str,
        secret: str,
        payload: PromptPayload,
        prompt_version: str,
    ) -> tuple[object, dict[str, int], int]:
        """按任务类型调用对应方法；新增任务类型时在这里加一个分支。"""

        if task_kind == TaskType.TRANSLATE_MESSAGE.value:
            return self.provider.translate(provider, model_name, secret, payload, prompt_version)
        if task_kind == TaskType.ENRICH_MESSAGE.value:
            return self.provider.enrich(provider, model_name, secret, payload, prompt_version)
        raise DomainError("task_kind_unsupported", f"任务类型尚未支持试跑：{task_kind}")


def _output(result: object) -> dict[str, Any]:
    """结构化结果转成可展示字典；字段由任务类型的 schema 决定。"""

    return asdict(cast(TranslationResult | EnrichmentResult, result))
