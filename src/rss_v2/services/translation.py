"""英文消息翻译：固定输入版本、有限 Key 切换和逐次调用审计。"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from rss_v2.domain import (
    DomainError,
    ExternalServiceError,
    LLMCall,
    MessageVersion,
    Provider,
    ProviderKey,
    Task,
    TaskStatus,
    TaskType,
    Translation,
    TranslationResult,
)
from rss_v2.domain.values import new_id, now
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
    secret_available: Callable[[str], bool]  # 检查外部环境，密钥值不进入业务对象

    def create_task(self, message_id: str) -> Task:
        """为最新英文版本创建或复用任务，快照模型与提示词版本。"""
        message = self.messages.get_message(message_id)
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        version = self.messages.latest_version(message.id)
        if version is None:
            raise DomainError("message_empty", "消息没有可翻译版本")
        if version.language.value != "en":
            raise DomainError("not_english", "只有英文消息可以创建中文翻译")
        providers = self.llm_config.list_providers(enabled_only=True)
        # 已有任务的读取不依赖当前 Key 的冷却或环境变量。
        for candidate in providers:
            existing = self.tasks.get_by_idempotency(
                f"translate:{version.id}:{candidate.model}:{self.prompt_version}"
            )
            if existing is not None:
                return existing
        provider = next((item for item in providers if self._keys(item)), None)
        if provider is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型 provider")
        return self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.TRANSLATE_MESSAGE,
                idempotency_key=f"translate:{version.id}:{provider.model}:{self.prompt_version}",
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=version.id,
                output_version_id=None,
                payload={
                    "message_id": message.id,
                    "provider_id": provider.id,
                    "model": provider.model,
                    "prompt_version": self.prompt_version,
                },
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
        )

    def run(self, task: Task) -> Translation:
        """执行任务的历史版本；成功结果可复用，依次切换 Key 与 Provider。"""
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
            version.id, prompt, str(task.payload["model"])
        )
        if existing is not None and existing.status == "succeeded":
            return existing
        providers = self._providers(task)
        if not providers:
            raise DomainError("llm_not_configured", "没有启用的模型 provider")
        last_error: DomainError = DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        for candidate in providers:
            for key in self._keys(candidate):
                try:
                    return self._execute_key(task, version, candidate, key, prompt)
                except DomainError as exc:
                    if exc.code == "task_lease_lost":
                        raise
                    last_error = exc
        self._save_result(task, version, providers[0], None, prompt, None, last_error)
        raise last_error

    def _input_version(self, task: Task) -> MessageVersion:
        if task.input_version_id is None:
            raise DomainError("task_invalid", "翻译任务缺少输入版本")
        version = self.messages.get_version(task.input_version_id)
        if version is None or version.message_id != task.payload.get("message_id"):
            raise DomainError("version_changed", "任务对应的消息版本已不存在")
        return version

    def _providers(self, task: Task) -> list[Provider]:
        preferred = str(task.payload.get("provider_id", ""))
        # 首选模型在入队时快照；修改配置不改变已排队任务的模型名。
        candidates = [
            replace(candidate, model=str(task.payload["model"]))
            if candidate.id == preferred
            else candidate
            for candidate in self.llm_config.list_providers(enabled_only=True)
        ]
        candidates.sort(key=lambda item: (item.id != preferred, item.priority, item.id))
        return candidates

    def _execute_key(
        self,
        task: Task,
        version: MessageVersion,
        candidate: Provider,
        key: ProviderKey,
        prompt: str,
    ) -> Translation:
        started = time.perf_counter()
        try:
            result, tokens, duration = self.provider.translate(
                candidate, key.key_ref, version.title, version.summary, version.content, prompt
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
                key,
                prompt,
                int((time.perf_counter() - started) * 1000),
                {},
                error,
            )
            cooldown = (
                now() + self.cooldown_seconds
                if error.code in {"rate_limited", "llm_timeout", "llm_server_error"}
                else None
            )
            self.llm_config.mark_key(key.key_ref, error.code, cooldown)
            raise error from None
        self._audit(task, version, candidate, key, prompt, duration, tokens, None)
        self.llm_config.mark_key(key.key_ref, "succeeded", None)
        return self._save_result(task, version, candidate, key, prompt, result, None)

    def _audit(
        self,
        task: Task,
        version: MessageVersion,
        provider: Provider,
        key: ProviderKey,
        prompt: str,
        duration: int,
        tokens: dict[str, int],
        error: DomainError | None,
    ) -> None:
        """只持久化哈希与调用元数据，不写入完整原文或密钥。"""
        input_hash = hashlib.sha256(
            f"{version.title}\n{version.summary}\n{version.content}".encode()
        ).hexdigest()
        self.llm_calls.add(
            LLMCall(
                new_id(),
                task.id,
                provider.id,
                key.key_ref,
                provider.model,
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
                key_ref=key.key_ref if key else None,
                model=provider.model,
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
        """仓储按优先级排序，过滤停用、冷却和环境中缺失的 Key。"""
        current = now()
        return [
            key
            for key in self.llm_config.list_keys(provider.id, enabled_only=True)
            if (key.cooldown_until is None or key.cooldown_until <= current)
            and self.secret_available(key.key_ref)
        ]

    def list_for_version(self, version_id: str) -> list[Translation]:
        """返回版本已有的译文。"""
        return self.translations.list_for_version(version_id)
