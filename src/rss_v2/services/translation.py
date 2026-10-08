"""英文消息翻译：固定输入版本、有限 Key 切换和逐次调用审计。"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from rss_v2.domain import (
    ConnectionTest,
    DomainError,
    ExternalServiceError,
    LLMCall,
    MessageVersion,
    Provider,
    ProviderKey,
    ProviderModel,
    Task,
    TaskStatus,
    TaskType,
    Translation,
    TranslationResult,
)
from rss_v2.domain.values import mask_secret, new_id, now
from rss_v2.llm import LLMProvider
from rss_v2.ports import (
    LLMCallRepository,
    LLMConfigRepository,
    MessageRepository,
    TaskRepository,
    TranslationRepository,
)


@dataclass(slots=True)
class TranslationService:
    """API 入队，worker 调用模型；依赖全部由组合根传入。"""

    messages: MessageRepository  # 原文与不可变版本
    translations: TranslationRepository  # 译文持久化
    tasks: TaskRepository  # 幂等任务与结果关联
    llm_config: LLMConfigRepository  # Provider 与 Key 引用
    llm_calls: LLMCallRepository  # 调用元数据审计
    provider: LLMProvider  # 唯一模型调用协议
    prompt_version: str  # 当前提示词版本标识
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
        # 已有任务的读取不依赖当前 Key 的冷却或环境变量。
        for _, model in self._enabled_pairs():
            existing = self.tasks.get_by_idempotency(
                f"translate:{version.id}:{model.model}:{self.prompt_version}"
            )
            if existing is not None:
                return existing
        candidate = next((item for item in self._enabled_pairs() if self._keys(item[0])), None)
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        provider, model = candidate
        return self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.TRANSLATE_MESSAGE,
                idempotency_key=f"translate:{version.id}:{model.model}:{self.prompt_version}",
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=version.id,
                output_version_id=None,
                payload={
                    "message_id": message.id,
                    "provider_id": provider.id,
                    "model": model.model,
                    "prompt_version": self.prompt_version,
                },
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
        )

    def run(self, task: Task) -> Translation:
        """执行任务的历史版本；成功结果可复用，依次切换 Key 与 Provider×模型。"""
        version = self._input_version(task)
        prompt = str(task.payload.get("prompt_version", self.prompt_version))
        completed = next(
            (
                item
                for item in self.translations.list_for_version(version.id)
                if item.task_id == task.id and item.status == "succeeded"
            ),
            None,
        )
        existing = completed or self.translations.get_for_version(
            version.id, prompt, str(task.payload.get("model", ""))
        )
        if existing is not None and existing.status == "succeeded":
            return existing
        candidates = self._candidates(task)
        if not candidates:
            raise DomainError("llm_not_configured", "没有启用的模型")
        last_error: DomainError = DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        for candidate, model_name in candidates:
            for key in self._keys(candidate):
                try:
                    return self._execute_key(task, version, candidate, model_name, key, prompt)
                except DomainError as exc:
                    if exc.code == "task_lease_lost":
                        raise
                    last_error = exc
        self._save_result(
            task, version, candidates[0][0], candidates[0][1], None, prompt, None, last_error
        )
        raise last_error

    def _input_version(self, task: Task) -> MessageVersion:
        if task.input_version_id is None:
            raise DomainError("task_invalid", "翻译任务缺少输入版本")
        version = self.messages.get_version(task.input_version_id)
        if version is None or version.message_id != task.payload.get("message_id"):
            raise DomainError("version_changed", "任务对应的消息版本已不存在")
        return version

    def _enabled_pairs(self) -> list[tuple[Provider, ProviderModel]]:
        """当前启用的 Provider×模型候选，仓储顺序已稳定。"""

        return [
            (provider, model)
            for provider in self.llm_config.list_providers(enabled_only=True)
            for model in self.llm_config.list_models(provider.id, enabled_only=True)
        ]

    def _candidates(self, task: Task) -> list[tuple[Provider, str]]:
        """首选组合在入队时快照；修改配置不改变已排队任务的模型名。"""
        preferred_provider = str(task.payload.get("provider_id", ""))
        preferred_model = str(task.payload.get("model", ""))
        rest = sorted(
            (
                (provider, model)
                for provider, model in self._enabled_pairs()
                if not (provider.id == preferred_provider and model.model == preferred_model)
            ),
            key=lambda item: (item[1].priority, item[1].created_at, item[0].id),
        )
        head: list[tuple[Provider, str]] = []
        if preferred_provider and preferred_model:
            provider = next(
                (
                    item
                    for item in self.llm_config.list_providers(enabled_only=True)
                    if item.id == preferred_provider
                ),
                None,
            )
            if provider is not None:
                head = [(provider, preferred_model)]
        return head + [(provider, model.model) for provider, model in rest]

    def test_connection(self, provider_id: str, model: str | None = None) -> ConnectionTest:
        """用一枚可用 Key 试跑一次最小结构化调用，返回延迟或错误。

        与真实翻译共用候选与冷却规则：失败会更新 Key 状态并在审计表留痕，
        方便在界面确认 Base URL、模型与密钥是否真的可用。
        """
        provider = self.llm_config.get_provider(provider_id)
        if provider is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        models = self.llm_config.list_models(provider_id, enabled_only=True)
        chosen = next((item for item in models if model is None or item.model == model), None)
        if chosen is None:
            raise DomainError("model_not_configured", "先在模型列表中添加并启用模型")
        keys = self._keys(provider)
        if not keys:
            raise DomainError("llm_key_not_configured", "没有启用且未冷却的 Key，请先添加 API Key")
        prompt = f"{self.prompt_version}+connection-test"
        probe_hash = hashlib.sha256(b"connection-test").hexdigest()
        last_error = DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        for key in keys:
            started = time.perf_counter()
            try:
                _, tokens, duration = self.provider.translate(
                    provider,
                    chosen.model,
                    key.secret,
                    "Connection test",
                    "",
                    "Reply that the connection works.",
                    prompt,
                )
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, DomainError)
                    else ExternalServiceError("llm_internal_error", "模型适配器发生内部错误")
                )
                self._record_call(
                    None,
                    provider,
                    chosen.model,
                    key,
                    prompt,
                    probe_hash,
                    int((time.perf_counter() - started) * 1000),
                    {},
                    error,
                )
                self.llm_config.mark_key(key.id, error.code, self._cooldown_until(error))
                last_error = error
                continue
            self._record_call(
                None, provider, chosen.model, key, prompt, probe_hash, duration, tokens, None
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

    def _execute_key(
        self,
        task: Task,
        version: MessageVersion,
        candidate: Provider,
        model_name: str,
        key: ProviderKey,
        prompt: str,
    ) -> Translation:
        started = time.perf_counter()
        try:
            result, tokens, duration = self.provider.translate(
                candidate,
                model_name,
                key.secret,
                version.title,
                version.summary,
                version.content,
                prompt,
            )
        except Exception as exc:
            if isinstance(exc, DomainError):
                error = exc
            else:
                logging.getLogger(__name__).error("llm_error class=%s", type(exc).__name__)
                error = ExternalServiceError("llm_internal_error", "模型适配器发生内部错误")
            self._audit(
                task,
                version,
                candidate,
                model_name,
                key,
                prompt,
                int((time.perf_counter() - started) * 1000),
                {},
                error,
            )
            self.llm_config.mark_key(key.id, error.code, self._cooldown_until(error))
            raise error from None
        self._audit(task, version, candidate, model_name, key, prompt, duration, tokens, None)
        self.llm_config.mark_key(key.id, "succeeded", None)
        return self._save_result(task, version, candidate, model_name, key, prompt, result, None)

    def _cooldown_until(self, error: DomainError) -> int | None:
        """临时错误让 Key 进入冷却；配置类错误不冷却，避免误停可用密钥。"""
        if error.code in {"rate_limited", "llm_timeout", "llm_server_error"}:
            return now() + self.cooldown_seconds
        return None

    def _audit(
        self,
        task: Task,
        version: MessageVersion,
        provider: Provider,
        model_name: str,
        key: ProviderKey,
        prompt: str,
        duration: int,
        tokens: dict[str, int],
        error: DomainError | None,
    ) -> None:
        """翻译调用审计：只持久化哈希与调用元数据，不写入完整原文或密钥。"""
        self._record_call(
            task.id,
            provider,
            model_name,
            key,
            prompt,
            hashlib.sha256(
                f"{version.title}\n{version.summary}\n{version.content}".encode()
            ).hexdigest(),
            duration,
            tokens,
            error,
        )

    def _record_call(
        self,
        task_id: str | None,
        provider: Provider,
        model_name: str,
        key: ProviderKey,
        prompt: str,
        input_hash: str,
        duration: int,
        tokens: dict[str, int],
        error: DomainError | None,
    ) -> None:
        """每次模型调用（含连接测试）写入审计；密钥只记录掩码。"""
        self.llm_calls.add(
            LLMCall(
                new_id(),
                task_id,
                provider.id,
                mask_secret(key.secret),
                model_name,
                prompt,
                input_hash,
                duration,
                tokens,
                "failed" if error else "succeeded",
                error.code if error else None,
                now(),
            )
        )

    def _save_result(
        self,
        task: Task,
        version: MessageVersion,
        provider: Provider,
        model_name: str,
        key: ProviderKey | None,
        prompt: str,
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
                prompt_version=prompt,
                task_id=task.id,
                error_code=error.code if error else None,
                error_message=error.message if error else None,
                created_at=now(),
                updated_at=now(),
            ),
            task.lease_token,
        )

    def _keys(self, provider: Provider) -> list[ProviderKey]:
        """仓储按优先级排序，过滤停用、冷却和密钥值为空的 Key。"""
        current = now()
        return [
            key
            for key in self.llm_config.list_keys(provider.id, enabled_only=True)
            if (key.cooldown_until is None or key.cooldown_until <= current) and key.secret.strip()
        ]

    def list_for_version(self, version_id: str) -> list[Translation]:
        """返回版本已有的译文。"""
        return self.translations.list_for_version(version_id)
