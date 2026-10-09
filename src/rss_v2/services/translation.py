"""英文消息翻译：固定输入版本、有限 Key 切换和逐次调用审计。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rss_v2.domain import (
    ConnectionTest,
    DomainError,
    ExternalServiceError,
    MessageVersion,
    Prompt,
    Provider,
    ProviderKey,
    Task,
    TaskStatus,
    TaskType,
    Translation,
    TranslationResult,
)
from rss_v2.domain.values import mask_secret, new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import (
    LLMConfigRepository,
    MessageRepository,
    TaskRepository,
    TranslationRepository,
)
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

#: 连接测试的固定探测文本；它只验证连通性，不使用用户提示词。
PROBE = PromptPayload(system="", user="Reply that the connection works.")


@dataclass(slots=True)
class TranslationService:
    """API 入队，worker 调用模型；依赖全部由组合根传入。"""

    messages: MessageRepository  # 原文与不可变版本
    translations: TranslationRepository  # 译文持久化
    tasks: TaskRepository  # 幂等任务与结果关联
    llm_config: LLMConfigRepository  # Provider、模型候选与 Key
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与调用审计
    prompts: PromptResolver  # 提示词与任务分配解析
    cooldown_seconds: int  # 临时错误冷却秒数

    def create_task(self, message_id: str) -> Task:
        """为最新英文版本创建或复用任务，快照 Provider×模型与提示词版本。"""
        message = self.messages.get_message(message_id)
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        version = self.messages.latest_version(message.id)
        if version is None:
            raise DomainError("message_empty", "消息没有可翻译版本")
        if version.language.value != "en":
            raise DomainError("not_english", "只有英文消息可以创建中文翻译")
        return self.create_task_for_version(version, message.source_id)

    def create_task_for_version(self, version: MessageVersion, source_id: str) -> Task:
        """为指定版本入队；采集后自动翻译与手动翻译共用同一幂等规则。"""
        prompt = self.prompt_for(source_id)
        # 已有任务的读取不依赖当前 Key 的冷却或环境变量。
        for model in self.failover.model_names():
            existing = self.tasks.get_by_idempotency(
                self.idempotency_key(version, model, prompt.version_string)
            )
            if existing is not None:
                return existing
        candidate = self.failover.first_available()
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        provider, model = candidate
        return self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.TRANSLATE_MESSAGE,
                idempotency_key=self.idempotency_key(version, model.model, prompt.version_string),
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=version.id,
                output_version_id=None,
                payload={
                    "message_id": version.message_id,
                    "provider_id": provider.id,
                    "model": model.model,
                    "prompt_id": prompt.id,
                    "prompt_version": prompt.version_string,
                },
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
        )

    def prompt_for(self, source_id: str) -> Prompt:
        """按来源配置取提示词；没有可用提示词时明确失败。"""

        resolved = self.prompts.resolve(source_id, TaskType.TRANSLATE_MESSAGE.value)
        if resolved.prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的翻译提示词")
        return resolved.prompt

    def idempotency_key(self, version: MessageVersion, model: str, prompt_version: str) -> str:
        """同一版本、同一模型、同一提示词版本只调用一次模型。"""

        return f"translate:{version.id}:{model}:{prompt_version}"

    def run(self, task: Task) -> Translation:
        """执行任务的历史版本；成功结果可复用，依次切换 Key 与 Provider×模型。"""

        version = self._input_version(task)
        prompt_version = str(task.payload.get("prompt_version", ""))
        payload = self.prompts.payload(
            self.prompts.snapshot(str(task.payload.get("prompt_id", ""))), version
        )
        completed = next(
            (
                item
                for item in self.translations.list_for_version(version.id)
                if item.task_id == task.id and item.status == "succeeded"
            ),
            None,
        )
        existing = completed or self.translations.get_for_version(
            version.id, prompt_version, str(task.payload.get("model", ""))
        )
        if existing is not None and existing.status == "succeeded":
            return existing

        def save(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: TranslationResult,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> Translation:
            return self._save_result(
                task, version, provider, model_name, key, prompt_version, result, None
            )

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[TranslationResult, dict[str, int], int]:
            return self.provider.translate(provider, model_name, secret, payload, prompt_version)

        try:
            return self.failover.execute(
                task.id,
                input_hash(payload.system, payload.user),
                prompt_version,
                self.cooldown_seconds,
                invoke,
                save,
            )
        except DomainError as exc:
            provider, model_name = self.failure_pair(task)
            self._save_result(task, version, provider, model_name, None, prompt_version, None, exc)
            raise

    def failure_pair(self, task: Task) -> tuple[Provider, str]:
        """失败记录沿用首选候选，与排队时的配置快照保持一致。"""

        candidates = self.failover.candidates(task.id)
        if candidates:
            return candidates[0]
        return (
            Provider("", "", "", False, 0.0, None, now(), now()),
            str(task.payload.get("model", "")),
        )

    def test_connection(self, provider_id: str, model: str | None = None) -> ConnectionTest:
        """用一枚可用 Key 试跑一次最小结构化调用，返回延迟或错误。

        探测文本固定，只验证连通性；版本串取全局默认翻译提示词并带可区分后缀，
        因此审计里能一眼认出这不是真实翻译。
        """
        provider = self.llm_config.get_provider(provider_id)
        if provider is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        models = self.llm_config.list_models(provider_id, enabled_only=True)
        chosen = next((item for item in models if model is None or item.model == model), None)
        if chosen is None:
            raise DomainError("model_not_configured", "先在模型列表中添加并启用模型")
        keys = self.failover.keys(provider)
        if not keys:
            raise DomainError("llm_key_not_configured", "没有启用且未冷却的 Key，请先添加 API Key")
        active = self.prompts.active(TaskType.TRANSLATE_MESSAGE.value)
        prompt_version = f"{active.version_string}+connection-test" if active else "connection-test"
        probe_hash = input_hash(PROBE.system, PROBE.user)
        last_error = DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        for key in keys:
            started = time.perf_counter()
            try:
                _, tokens, duration = self.provider.translate(
                    provider, chosen.model, key.secret, PROBE, prompt_version
                )
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, DomainError)
                    else ExternalServiceError("llm_internal_error", "模型适配器发生内部错误")
                )
                self.failover.record_call(
                    None,
                    provider,
                    chosen.model,
                    key,
                    prompt_version,
                    probe_hash,
                    int((time.perf_counter() - started) * 1000),
                    {},
                    error,
                )
                self.llm_config.mark_key(
                    key.id, error.code, self.failover.cooldown_until(error, self.cooldown_seconds)
                )
                last_error = error
                continue
            self.failover.record_call(
                None,
                provider,
                chosen.model,
                key,
                prompt_version,
                probe_hash,
                duration,
                tokens,
                None,
            )
            self.llm_config.mark_key(key.id, "succeeded", None)
            return ConnectionTest(
                True,
                chosen.model,
                mask_secret(key.secret),
                duration,
                int(tokens.get("total_tokens", 0)),
                None,
                None,
            )
        return ConnectionTest(False, chosen.model, "", 0, 0, last_error.code, last_error.message)

    def list_for_version(self, version_id: str) -> list[Translation]:
        """返回版本已有的译文。"""
        return self.translations.list_for_version(version_id)

    def _input_version(self, task: Task) -> MessageVersion:
        if task.input_version_id is None:
            raise DomainError("task_invalid", "翻译任务缺少输入版本")
        version = self.messages.get_version(task.input_version_id)
        if version is None or version.message_id != task.payload.get("message_id"):
            raise DomainError("version_changed", "任务对应的消息版本已不存在")
        return version

    def _save_result(
        self,
        task: Task,
        version: MessageVersion,
        provider: Provider,
        model_name: str,
        key: ProviderKey | None,
        prompt_version: str,
        result: TranslationResult | None,
        error: DomainError | None,
    ) -> Translation:
        """成功与失败共用版本关联和元数据存储。"""
        return self.translations.save(
            Translation(
                id=new_id(),
                message_version_id=version.id,
                status="succeeded" if result else "failed",
                title=result.title if result else None,
                summary=result.summary if result else None,
                content=result.content if result else None,
                provider_id=provider.id,
                key_masked=mask_secret(key.secret) if key else None,
                model=model_name,
                prompt_version=prompt_version,
                task_id=task.id,
                error_code=error.code if error else None,
                error_message=error.message if error else None,
                created_at=now(),
                updated_at=now(),
            ),
            task.lease_token,
        )
