"""英文消息翻译用例。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from rss_v2.adapters.sqlite.common import new_id, now
from rss_v2.domain import (
    DomainError,
    LLMCall,
    Provider,
    ProviderKey,
    Task,
    TaskStatus,
    TaskType,
    Translation,
)
from rss_v2.ports import (
    LLMCallRepository,
    LLMConfigRepository,
    LLMProvider,
    MessageRepository,
    TaskRepository,
    TranslationRepository,
)


@dataclass(slots=True)
class TranslationService:
    """创建翻译任务并在 worker 中完成翻译。"""

    messages: MessageRepository
    translations: TranslationRepository
    tasks: TaskRepository
    llm_config: LLMConfigRepository
    llm_calls: LLMCallRepository
    provider: LLMProvider
    prompt_version: str
    cooldown_seconds: int
    secret_available: Callable[[str], bool]

    def create_task(self, message_id: str) -> Task:
        message = self.messages.get_message(message_id)
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        version = self.messages.latest_version(message.id)
        if version is None:
            raise DomainError("message_empty", "消息没有可翻译版本")
        if version.language.value != "en":
            raise DomainError("not_english", "只有英文消息可以创建中文翻译")
        provider = self._select_provider()
        if provider is None:
            raise DomainError("llm_not_configured", "没有启用的模型 provider")
        key = self._select_key(provider)
        if key is None:
            raise DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        idempotency = f"translate:{version.id}:{provider.model}:{self.prompt_version}"
        existing = self.tasks.get_by_idempotency(idempotency)
        if existing is not None:
            return existing
        task = Task(
            id=new_id(),
            task_type=TaskType.TRANSLATE_MESSAGE,
            idempotency_key=idempotency,
            status=TaskStatus.QUEUED,
            attempts=0,
            lease_until=None,
            input_version_id=version.id,
            output_version_id=None,
            payload={"message_id": message.id, "provider_id": provider.id, "model": provider.model},
            error_code=None,
            error_message=None,
            created_at=now(),
            updated_at=now(),
        )
        return self.tasks.create(task)

    def run(self, task: Task) -> Translation:
        if task.input_version_id is None:
            raise DomainError("task_invalid", "翻译任务缺少输入版本")
        message = self.messages.get_message(str(task.payload.get("message_id", "")))
        if message is None:
            raise DomainError("message_not_found", "消息不存在")
        version = self.messages.latest_version(message.id)
        if version is None or version.id != task.input_version_id:
            raise DomainError("version_changed", "任务对应的消息版本已不存在")
        provider = self._select_provider(preferred_id=str(task.payload.get("provider_id", "")))
        if provider is None:
            raise DomainError("llm_not_configured", "没有启用的模型 provider")
        existing = self.translations.get_for_version(
            version.id, self.prompt_version, provider.model
        )
        if existing is not None and existing.status == "succeeded":
            return existing
        last_error: DomainError | None = None
        for candidate in self.llm_config.list_providers(enabled_only=True):
            for key in self._keys(candidate):
                if not self.secret_available(key.key_ref):
                    continue
                try:
                    result, token_usage, duration_ms = self.provider.translate(
                        candidate,
                        key.key_ref,
                        version.title,
                        version.summary,
                        version.content,
                        self.prompt_version,
                    )
                    self.llm_calls.add(
                        LLMCall(
                            new_id(),
                            task.id,
                            candidate.id,
                            key.key_ref,
                            candidate.model,
                            self.prompt_version,
                            self._input_hash(version.title, version.content),
                            duration_ms,
                            token_usage,
                            "succeeded",
                            None,
                            now(),
                        )
                    )
                    translation = Translation(
                        id=new_id(),
                        message_version_id=version.id,
                        status="succeeded",
                        title=result.title,
                        summary=result.summary,
                        content=result.content,
                        provider_id=candidate.id,
                        key_ref=key.key_ref,
                        model=candidate.model,
                        prompt_version=self.prompt_version,
                        task_id=task.id,
                        error_code=None,
                        error_message=None,
                        created_at=now(),
                        updated_at=now(),
                    )
                    self.llm_config.mark_key(key.key_ref, "succeeded", None)
                    return self.translations.save(translation)
                except DomainError as exc:
                    last_error = exc
                    self.llm_calls.add(
                        LLMCall(
                            new_id(),
                            task.id,
                            candidate.id,
                            key.key_ref,
                            candidate.model,
                            self.prompt_version,
                            self._input_hash(version.title, version.content),
                            0,
                            {},
                            "failed",
                            exc.code,
                            now(),
                        )
                    )
                    cooldown = (
                        now() + self.cooldown_seconds
                        if exc.code in {"rate_limited", "llm_timeout", "llm_server_error"}
                        else None
                    )
                    self.llm_config.mark_key(key.key_ref, exc.code, cooldown)
        if last_error is None:
            last_error = DomainError("llm_key_not_configured", "没有可用的模型 API Key")
        translation = Translation(
            id=new_id(),
            message_version_id=version.id,
            status="failed",
            title=None,
            summary=None,
            content=None,
            provider_id=provider.id,
            key_ref=None,
            model=provider.model,
            prompt_version=self.prompt_version,
            task_id=task.id,
            error_code=last_error.code,
            error_message=last_error.message,
            created_at=now(),
            updated_at=now(),
        )
        self.translations.save(translation)
        raise last_error

    def list_for_version(self, version_id: str) -> list[Translation]:
        return self.translations.list_for_version(version_id)

    def _select_provider(self, preferred_id: str | None = None) -> Provider | None:
        providers = self.llm_config.list_providers(enabled_only=True)
        if preferred_id:
            preferred = next((item for item in providers if item.id == preferred_id), None)
            if preferred is not None:
                return preferred
        return providers[0] if providers else None

    def _select_key(self, provider: Provider) -> ProviderKey | None:
        keys = self._keys(provider)
        return keys[0] if keys else None

    def _keys(self, provider: Provider) -> list[ProviderKey]:
        current = now()
        return [
            key
            for key in self.llm_config.list_keys(provider.id, enabled_only=True)
            if key.cooldown_until is None or key.cooldown_until <= current
        ]

    @staticmethod
    def _input_hash(title: str, content: str) -> str:
        return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()
