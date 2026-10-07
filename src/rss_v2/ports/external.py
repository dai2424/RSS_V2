"""外部服务端口。"""

from __future__ import annotations

from typing import Protocol

from rss_v2.domain import FeedSnapshot, Provider, TranslationResult


class FeedClient(Protocol):
    """RSS 获取和解析端口。"""

    def fetch(self, url: str, timeout_seconds: float) -> FeedSnapshot: ...


class LLMProvider(Protocol):
    """业务层唯一可使用的模型调用端口。"""

    def translate(
        self,
        provider: Provider,
        key_ref: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]: ...


class SecretResolver(Protocol):
    """根据引用名解析外部密钥。"""

    def resolve(self, key_ref: str) -> str: ...
