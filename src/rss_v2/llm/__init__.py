"""业务调用 LLM 的唯一端口。"""

from typing import Protocol

from rss_v2.domain import EnrichmentResult, Provider, TranslationResult


class LLMProvider(Protocol):
    """业务层唯一可使用的模型调用端口。"""

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]: ...

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]: ...
