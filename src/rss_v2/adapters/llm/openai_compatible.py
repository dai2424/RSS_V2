"""OpenAI 兼容模型 Provider。"""

from __future__ import annotations

import os
import time
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rss_v2.domain import ExternalServiceError, Provider, TranslationResult


class TranslationResponse(BaseModel):
    """模型返回的结构化翻译结果。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    summary: str
    content: str


class ContentPart(BaseModel):
    """兼容多段文本响应。"""

    text: str = ""


class ChatMessage(BaseModel):
    """模型消息。"""

    content: str | list[ContentPart]


class ChatChoice(BaseModel):
    """候选模型响应。"""

    message: ChatMessage


class Usage(BaseModel):
    """兼容缺省 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """兼容协议的响应边界。"""

    choices: list[ChatChoice] = Field(min_length=1)
    usage: Usage = Field(default_factory=Usage)


class EnvironmentSecretResolver:
    """从环境变量读取 API Key。"""

    def resolve(self, key_ref: str) -> str:
        normalized = "".join(char if char.isalnum() else "_" for char in key_ref.upper())
        value = os.getenv(f"RSS_LLM_KEY_{normalized}", "").strip()
        if not value:
            raise ExternalServiceError("llm_key_missing", f"未配置模型密钥引用：{key_ref}")
        return value

    def available(self, key_ref: str) -> bool:
        normalized = "".join(char if char.isalnum() else "_" for char in key_ref.upper())
        return bool(os.getenv(f"RSS_LLM_KEY_{normalized}", "").strip())


class OpenAICompatibleProvider:
    """使用 OpenAI chat completions 协议调用翻译模型。"""

    def __init__(self, secrets: EnvironmentSecretResolver) -> None:
        self.secrets = secrets

    def translate(
        self,
        provider: Provider,
        key_ref: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        key = self.secrets.resolve(key_ref)
        endpoint = provider.base_url.rstrip("/") + "/chat/completions"
        prompt = (
            "你是专业科技资讯翻译。请把以下英文 RSS 内容翻译成简洁、准确的简体中文。"
            "必须只返回 JSON 对象，字段严格为 title、summary、content，不要 Markdown 包裹。\n"
            f"标题：{title}\n摘要：{summary}\n正文：{content}"
        )
        headers = {
            **provider.extra_headers,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if provider.session_header_name:
            headers[provider.session_header_name] = str(uuid.uuid4())
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": f"translation prompt version: {prompt_version}"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        try:
            response = httpx.post(
                endpoint, headers=headers, json=payload, timeout=provider.timeout_seconds
            )
        except httpx.TimeoutException as exc:
            raise ExternalServiceError("llm_timeout", "模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError("llm_network_error", "模型网络请求失败") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 429:
            raise ExternalServiceError("rate_limited", "模型请求频率受限")
        if response.status_code >= 500:
            raise ExternalServiceError(
                "llm_server_error", f"模型服务返回 HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise ExternalServiceError(
                "llm_request_error", f"模型请求被拒绝（HTTP {response.status_code}）"
            )

        parsed, token_usage = self._parse_response(response)
        return (
            TranslationResult(parsed.title, parsed.summary, parsed.content),
            token_usage,
            duration_ms,
        )

    @staticmethod
    def _parse_response(response: httpx.Response) -> tuple[TranslationResponse, dict[str, int]]:
        """校验协议 envelope 与模型 JSON；不记录返回正文。"""
        try:
            body = ChatResponse.model_validate_json(response.content)
            raw = body.choices[0].message.content
            raw_content = raw if isinstance(raw, str) else "".join(item.text for item in raw)
            parsed = TranslationResponse.model_validate_json(raw_content)
        except ValidationError as exc:
            raise ExternalServiceError("llm_invalid_output", "模型返回的翻译结构无法校验") from exc
        token_usage = body.usage.model_dump()
        return parsed, token_usage
