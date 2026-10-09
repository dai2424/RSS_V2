"""Anthropic Messages 协议 Provider（/v1/messages）。

与 chat completions 的差别集中在协议边界：端点是 `/messages`，系统提示走顶层
`system` 字段而不是 system 消息，认证头是 `x-api-key`，输出上限必须显式给出，
响应文本在 `content[]` 数组里、用量字段叫 input/output tokens。
结构化输出、错误分类与密钥脱敏复用 `adapters/llm/boundary.py`。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from rss_v2.adapters.llm.boundary import (
    PROBE_TASK,
    PROBE_VERSION,
    USER_AGENT,
    EnrichmentResponse,
    SchemaT,
    TranslationResponse,
    error_detail,
    json_output,
    keywords,
    parse_envelope,
    post_json,
    raise_for_status,
    reply_snippet,
    stable_session_id,
    system_message,
    validation_detail,
)
from rss_v2.domain import (
    EnrichmentResult,
    ExternalServiceError,
    Provider,
    TaskType,
    TranslationResult,
)
from rss_v2.llm import PromptPayload

#: Anthropic 协议要求的 API 版本头；取值自官方文档，兼容网关同样接受。
ANTHROPIC_VERSION = "2023-06-01"

#: 输出上限：Anthropic 协议必填，不像 chat completions 可以留给上游默认值。
#: 8192 是各代 Claude 模型都支持的上限，译文与摘要都用不到更多。
MAX_OUTPUT_TOKENS = 8192

#: 协议里"正常结束"的取值；其余（如 max_tokens）说明输出被截断，需要写进错误信息。
NORMAL_STOP = "end_turn"

#: 探测请求的输出上限：只要一句话，开小一点更快也更省。
PROBE_MAX_TOKENS = 64


class TextBlock(BaseModel):
    """响应内容块；只取文本，其它类型（如 thinking）忽略。"""

    type: str = "text"
    text: str = ""


class MessagesUsage(BaseModel):
    """Anthropic 的用量字段名与 chat completions 不同，这里先原样接收。"""

    input_tokens: int = 0
    output_tokens: int = 0


class MessagesResponse(BaseModel):
    """Messages 协议的响应边界。"""

    content: list[TextBlock] = Field(min_length=1)
    stop_reason: str | None = None
    usage: MessagesUsage = Field(default_factory=MessagesUsage)


def _token_usage(usage: MessagesUsage) -> dict[str, int]:
    """折算成与 chat completions 一致的字段，审计表按协议比较时不会错位。"""

    return {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.input_tokens + usage.output_tokens,
    }


def _truncation(stop_reason: str) -> str:
    """把协议里"正常结束"折算成空串，其余取值原样交给错误信息。

    `validation_detail` 只关心"是不是被截断"，两种协议的正常结束标记不同。
    """

    return "" if stop_reason in {"", NORMAL_STOP} else stop_reason


class AnthropicMessagesProvider:
    """使用 Anthropic Messages 协议调用翻译与内容加工模型。"""

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        parsed, tokens, duration = self._complete(
            provider,
            model,
            secret,
            prompt,
            TaskType.TRANSLATE_MESSAGE.value,
            prompt_version,
            TranslationResponse,
        )
        return TranslationResult(parsed.title, parsed.summary, parsed.content), tokens, duration

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]:
        parsed, tokens, duration = self._complete(
            provider,
            model,
            secret,
            prompt,
            TaskType.ENRICH_MESSAGE.value,
            prompt_version,
            EnrichmentResponse,
        )
        result = EnrichmentResult(parsed.title, parsed.summary, keywords(parsed.keywords))
        return result, tokens, duration

    def probe(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
    ) -> tuple[str, dict[str, int], int]:
        """连通性探测：最小请求 + 只校验 Messages envelope，不要求结构化输出。"""

        endpoint = provider.base_url.rstrip("/") + "/messages"
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": PROBE_MAX_TOKENS,
            "system": system_message(prompt, PROBE_TASK, PROBE_VERSION),
            "messages": [{"role": "user", "content": prompt.user}],
        }
        started = time.perf_counter()
        response = post_json(
            endpoint, self._headers(provider, secret), payload, provider.timeout_seconds
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        raise_for_status(response, secret)
        body = parse_envelope(response, MessagesResponse, secret)
        text = "".join(block.text for block in body.content)
        return reply_snippet(text), _token_usage(body.usage), duration_ms

    @staticmethod
    def _headers(provider: Provider, secret: str) -> dict[str, str]:
        """请求头：认证、协议版本、自定义头与会话头。"""

        headers = {
            "User-Agent": USER_AGENT,
            **provider.extra_headers,
            # 官方 API 认 x-api-key，兼容网关常直接复用 Authorization: Bearer；
            # 两份都带上，同一套配置既能连官方也能连网关（同一主机的同一次 TLS 请求）。
            "x-api-key": secret,
            "Authorization": f"Bearer {secret}",
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        if provider.session_header_name:
            # 兼容网关（如 opencode.ai/zen）要求会话 ID 在会话内稳定，
            # 用于路由亲和与 prompt 缓存；按 Provider 派生可跨重启保持一致。
            headers[provider.session_header_name] = stable_session_id(provider.id)
        return headers

    def _complete(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        task_kind: str,
        prompt_version: str,
        schema: type[SchemaT],
    ) -> tuple[SchemaT, dict[str, int], int]:
        """发送一次结构化请求并校验响应；翻译与加工共用同一协议边界。"""

        endpoint = provider.base_url.rstrip("/") + "/messages"
        headers = self._headers(provider, secret)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system_message(prompt, task_kind, prompt_version),
            "messages": [{"role": "user", "content": prompt.user}],
            "temperature": 0.2,
        }
        started = time.perf_counter()
        response = post_json(endpoint, headers, payload, provider.timeout_seconds)
        if response.status_code == 400:
            # 兼容网关常拒绝可选参数（temperature / max_tokens 上限）；去掉可选参数重试一次，
            # 结构化输出仍由 Pydantic 校验和有限重试兜底。
            logging.getLogger(__name__).info(
                "llm_optional_params_rejected protocol=anthropic_messages model=%s detail=%s",
                model,
                error_detail(response, secret),
            )
            retry_payload = {key: value for key, value in payload.items() if key != "temperature"}
            response = post_json(endpoint, headers, retry_payload, provider.timeout_seconds)
        duration_ms = int((time.perf_counter() - started) * 1000)
        raise_for_status(response, secret)

        parsed, token_usage = self._parse_response(response, schema, secret)
        return parsed, token_usage, duration_ms

    @staticmethod
    def _parse_response(
        response: httpx.Response, schema: type[SchemaT], secret: str
    ) -> tuple[SchemaT, dict[str, int]]:
        """校验 Messages envelope 与模型 JSON；失败时留下字段与返回片段供排查。"""

        raw_content = ""
        stop_reason = ""
        try:
            body = MessagesResponse.model_validate_json(response.content)
            stop_reason = body.stop_reason or ""
            raw_content = "".join(block.text for block in body.content)
            parsed = schema.model_validate_json(json_output(raw_content))
        except ValidationError as exc:
            detail = validation_detail(
                exc, raw_content or response.text, secret, _truncation(stop_reason)
            )
            raise ExternalServiceError(
                "llm_invalid_output", f"模型返回的结构无法校验：{detail}"
            ) from exc
        return parsed, _token_usage(body.usage)
