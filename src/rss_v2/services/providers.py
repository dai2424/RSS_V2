"""模型 provider 配置用例。"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.adapters.sqlite.common import new_id, now
from rss_v2.domain import DomainError, Provider, ProviderKey
from rss_v2.ports import LLMConfigRepository


@dataclass(slots=True)
class ProviderService:
    """管理非敏感 provider 和 key 引用。"""

    config: LLMConfigRepository

    def list(self) -> list[tuple[Provider, list[ProviderKey]]]:
        return [
            (provider, self.config.list_keys(provider.id))
            for provider in self.config.list_providers()
        ]

    def keys(self, provider_id: str) -> list[ProviderKey]:
        return self.config.list_keys(provider_id)

    def create(
        self,
        name: str,
        base_url: str,
        model: str,
        priority: int,
        timeout_seconds: float,
        session_header_name: str | None,
    ) -> Provider:
        if not base_url.startswith(("http://", "https://")):
            raise DomainError("invalid_provider_url", "模型 Base URL 必须是 HTTP(S) 地址")
        timestamp = now()
        return self.config.create_provider(
            Provider(
                new_id(),
                name.strip(),
                base_url.rstrip("/"),
                model.strip(),
                True,
                priority,
                timeout_seconds,
                session_header_name,
                timestamp,
                timestamp,
            )
        )

    def update(self, provider_id: str, changes: dict[str, object]) -> Provider:
        provider = self.config.get_provider(provider_id)
        if provider is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        if (
            "base_url" in changes
            and isinstance(changes["base_url"], str)
            and not changes["base_url"].startswith(("http://", "https://"))
        ):
            raise DomainError("invalid_provider_url", "模型 Base URL 必须是 HTTP(S) 地址")
        return self.config.update_provider(provider_id, changes)

    def add_key(self, provider_id: str, key_ref: str, priority: int) -> ProviderKey:
        if self.config.get_provider(provider_id) is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        timestamp = now()
        try:
            return self.config.create_key(
                ProviderKey(
                    new_id(), provider_id, key_ref, priority, True, None, None, timestamp, timestamp
                )
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise DomainError("key_ref_exists", "Key 引用名已经存在") from exc
            raise

    def update_key(self, provider_id: str, key_id: str, changes: dict[str, object]) -> ProviderKey:
        if self.config.get_provider(provider_id) is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        try:
            return self.config.update_key(key_id, changes)
        except KeyError as exc:
            raise DomainError("key_not_found", "模型 Key 引用不存在") from exc
