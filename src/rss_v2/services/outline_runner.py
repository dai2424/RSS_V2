"""一次提纲生成的执行器：渲染提示词 → 调用模型 → 输出结构化提纲草稿。

与用例层（`services/outlines.py`）分开：用例层管任务入队、版本落库与项目状态，
这里只管"一次执行怎么跑"。模型与提示词版本都取自任务快照，不重新挑选——任务排队
期间换了提示词或模型，不会改变这次运行的输入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rss_v2.domain import OutlineDraft, OutlineLimits, Prompt, Provider, ProviderKey, Task, TaskType
from rss_v2.domain.prompts import render
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OutlineRunner:
    """执行单个提纲生成任务；模型调用走 LLMFailover 与调用审计。"""

    prompts: PromptResolver  # 任务快照里的提示词版本
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与审计
    cooldown_seconds: int  # Key 冷却时长（秒）
    limits: OutlineLimits = OutlineLimits()

    def run(self, task: Task, *, title: str, summary: str, facts: str, notes: str) -> OutlineDraft:
        """调用模型生成提纲草稿；结构由 `OutlineDraft` 校验，业务规则由调用方过滤。

        提示词里没有可切分的批次：一次提纲生成只调用一次模型。因此这里不做分批，
        输出被截断（`llm_output_truncated`）直接失败——重试仍会撞同一个上限，需要
        的是提高 `outline_max_output_tokens` 或改提示词，而不是悄悄拆成两半。
        """

        prompt = self._prompt(task)
        values = {"title": title, "summary": summary, "facts": facts, "notes": notes}
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[OutlineDraft, dict[str, int], int]:
            return self.provider.structured(
                provider,
                model_name,
                secret,
                payload,
                prompt.version_string,
                TaskType.GENERATE_OUTLINE.value,
                OutlineDraft,
                self.limits.max_output_tokens,
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: OutlineDraft,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> OutlineDraft:
            return result

        return self.failover.execute(
            task.id,
            input_hash(payload.system, payload.user),
            prompt.version_string,
            self.cooldown_seconds,
            invoke,
            persist,
        )

    def _prompt(self, task: Task) -> Prompt:
        return self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
