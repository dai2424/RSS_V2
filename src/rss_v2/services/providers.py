"""模型 provider、模型候选和 API Key 配置用例。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from rss_v2.domain import ConflictError, DomainError, Provider, ProviderKey, ProviderModel
from rss_v2.domain.values import new_id, now, validate_http_url
from rss_v2.ports import LLMConfigRepository


@dataclass(slots=True)
class ProviderService:
    """管理非敏感 provider、模型候选和 key 引用。"""

    config: LLMConfigRepository

    def list(self) -> list[tuple[Provider, list[ProviderModel], list[ProviderKey]]]:
        """返回 provider 与其模型候选、API Key，按创建时间稳定排序。"""

        return [
            (
                provider,
                self.config.list_models(provider.id),
                self.config.list_keys(provider.id),
            )
            for provider in self.config.list_providers()
        ]

    def models(self, provider_id: str) -> list[ProviderModel]:
        return self.config.list_models(provider_id)

    def keys(self, provider_id: str) -> list[ProviderKey]:
        return self.config.list_keys(provider_id)

    def create(
        self,
        name: str,
        base_url: str,
        timeout_seconds: float,
        session_header_name: str | None,
        extra_headers: dict[str, str] | None = None,
    ) -> Provider:
        base_url = validate_http_url(base_url)
        self.validate_session_header(session_header_name)
        if not name.strip():
            raise DomainError("invalid_provider", "Provider 名称不能为空")
        timestamp = now()
        return self.config.create_provider(
            Provider(
                new_id(),
                name.strip(),
                base_url.rstrip("/"),
                True,
                timeout_seconds,
                session_header_name,
                timestamp,
                timestamp,
                self.validate_headers(extra_headers or {}),
            )
        )

    def update(self, provider_id: str, changes: dict[str, object]) -> Provider:
        provider = self.config.get_provider(provider_id)
        if provider is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        if isinstance(changes.get("base_url"), str):
            changes["base_url"] = validate_http_url(str(changes["base_url"])).rstrip("/")
        if "session_header_name" in changes:
            self.validate_session_header(changes["session_header_name"])
        if "name" in changes and not str(changes["name"]).strip():
            raise DomainError("invalid_provider", "Provider 名称不能为空")
        if "extra_headers" in changes:
            changes["extra_headers"] = self.validate_headers(changes["extra_headers"])
        return self.config.update_provider(provider_id, changes)

    def create_model(self, provider_id: str, model: str, priority: int) -> ProviderModel:
        if self.config.get_provider(provider_id) is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        if not model.strip():
            raise DomainError("invalid_provider", "模型 ID 不能为空")
        timestamp = now()
        try:
            return self.config.create_model(
                ProviderModel(
                    new_id(), provider_id, model.strip(), True, priority, timestamp, timestamp
                )
            )
        except ConflictError as exc:
            raise DomainError("model_exists", "当前 Provider 下已存在此模型 ID") from exc

    def update_model(
        self, provider_id: str, model_id: str, changes: dict[str, object]
    ) -> ProviderModel:
        if self.config.get_provider(provider_id) is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        existing = self.config.get_model(model_id)
        if existing is None or existing.provider_id != provider_id:
            raise DomainError("model_not_found", "当前 provider 下没有此模型")
        if "model" in changes and not str(changes["model"]).strip():
            raise DomainError("invalid_provider", "模型 ID 不能为空")
        try:
            return self.config.update_model(model_id, changes)
        except ConflictError as exc:
            raise DomainError("model_exists", "当前 Provider 下已存在此模型 ID") from exc

    def add_key(self, provider_id: str, secret: str, priority: int) -> ProviderKey:
        """录入一枚 API Key；密钥值只写入本机数据库，不出现在响应中。"""

        if self.config.get_provider(provider_id) is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        secret = self.validate_secret(secret)
        timestamp = now()
        return self.config.create_key(
            ProviderKey(
                new_id(), provider_id, secret, priority, True, None, None, timestamp, timestamp
            )
        )

    def update_key(self, provider_id: str, key_id: str, changes: dict[str, object]) -> ProviderKey:
        if self.config.get_provider(provider_id) is None:
            raise DomainError("provider_not_found", "模型 provider 不存在")
        if not any(key.id == key_id for key in self.config.list_keys(provider_id)):
            raise DomainError("key_not_found", "当前 provider 下没有此 Key")
        if "secret" in changes:
            changes["secret"] = self.validate_secret(changes["secret"])
        try:
            return self.config.update_key(key_id, changes)
        except KeyError as exc:
            raise DomainError("key_not_found", "模型 Key 不存在") from exc

    @staticmethod
    def validate_secret(secret: object) -> str:
        """密钥必须是非空文本；拒绝控制字符，避免写入请求头时被拆行。"""
        text = str(secret).strip()
        if not text or len(text) > 500 or any(char in text for char in "\r\n"):
            raise DomainError("invalid_key", "API Key 不能为空，且不能包含换行")
        return text

    @staticmethod
    def validate_session_header(name: object) -> None:
        """会话头不能覆盖认证或协议头。"""
        if name is None:
            return
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", name)
            or name.lower() in {"authorization", "cookie", "host", "content-type", "x-api-key"}
        ):
            raise DomainError("invalid_headers", "会话头名称无效")

    @staticmethod
    def validate_headers(headers: object) -> dict[str, str]:
        """自定义头仅用于非敏感兼容参数，认证头必须通过 API Key 生成。"""
        if not isinstance(headers, dict):
            raise DomainError("invalid_headers", "请求头必须是键值对象")
        clean: dict[str, str] = {}
        # isinstance 已限定为字典；逐项验证名称和文本，不依赖未经检查的值类型。
        for key, value in cast(dict[str, Any], headers).items():
            name, text = str(key), str(value)
            if (
                name.lower() in {"authorization", "cookie", "x-api-key", "api-key", "host"}
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", name)
                or "\r" in text
                or "\n" in text
            ):
                raise DomainError("invalid_headers", "请求头不能包含密钥、认证信息或换行")
            clean[name] = text
        return clean
