"""业务调用 LLM 的唯一端口。"""

from dataclasses import dataclass
from typing import Protocol

from rss_v2.domain import EnrichmentResult, Provider, TranslationResult


@dataclass(frozen=True, slots=True)
class PromptPayload:
    """渲染完成的提示词。

    模板、占位符与版本选择都在服务层完成，端口只接收最终文本，适配器不再持有
    任何提示词内容。
    """

    system: str  # 系统提示；为空时适配器只发送任务标记
    user: str  # 用户提示


class LLMProvider(Protocol):
    """业务层唯一可使用的模型调用端口。"""

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]: ...

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]: ...
