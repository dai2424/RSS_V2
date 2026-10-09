"""按 Provider 配置的协议分发到具体适配器。"""

from __future__ import annotations

from collections.abc import Mapping

from rss_v2.domain import (
    EnrichmentResult,
    ExternalServiceError,
    Provider,
    TranslationResult,
)
from rss_v2.llm import LLMProvider, PromptPayload


class ProtocolRouter:
    """业务层只依赖 LLMProvider 端口；具体协议实现由组合根注册。

    未知协议（例如数据库里被手改成未实现的取值）明确报错而不是回退默认协议：
    用错协议发出的请求只会得到难以解释的上游错误，还会写进审计。
    """

    def __init__(self, protocols: Mapping[str, LLMProvider]) -> None:
        self.protocols = dict(protocols)

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        return self._for(provider).translate(provider, model, secret, prompt, prompt_version)

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]:
        return self._for(provider).enrich(provider, model, secret, prompt, prompt_version)

    def _for(self, provider: Provider) -> LLMProvider:
        implementation = self.protocols.get(provider.protocol)
        if implementation is None:
            raise ExternalServiceError(
                "llm_protocol_unsupported", f"Provider 协议尚未实现：{provider.protocol}"
            )
        return implementation
