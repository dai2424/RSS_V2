"""OpenAI 兼容模型 Provider（chat completions）。"""

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


class ContentPart(BaseModel):
    """兼容多段文本响应。"""

    text: str = ""


class ChatMessage(BaseModel):
    """模型消息。"""

    content: str | list[ContentPart]


class ChatChoice(BaseModel):
    """候选模型响应。"""

    message: ChatMessage
    finish_reason: str | None = None  # stop 正常结束；length 表示被输出上限截断


class Usage(BaseModel):
    """兼容缺省 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    """兼容协议的响应边界。"""

    choices: list[ChatChoice] = Field(min_length=1)
    usage: Usage = Field(default_factory=Usage)


class OpenAICompatibleProvider:
    """使用 OpenAI chat completions 协议调用翻译与内容加工模型。"""

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
        """连通性探测：最小请求 + 只校验协议 envelope，不要求结构化输出。"""

        endpoint = provider.base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_message(prompt, PROBE_TASK, PROBE_VERSION)},
                {"role": "user", "content": prompt.user},
            ],
        }
        started = time.perf_counter()
        response = post_json(
            endpoint, self._headers(provider, secret), payload, provider.timeout_seconds
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        raise_for_status(response, secret)
        body = parse_envelope(response, ChatResponse, secret)
        raw = body.choices[0].message.content
        text = raw if isinstance(raw, str) else "".join(item.text for item in raw)
        return reply_snippet(text), body.usage.model_dump(), duration_ms

    @staticmethod
    def _headers(provider: Provider, secret: str) -> dict[str, str]:
        """请求头：认证、用户代理、自定义头与会话头。"""

        headers = {
            "User-Agent": USER_AGENT,
            **provider.extra_headers,
            "Authorization": f"Bearer {secret}",
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

        endpoint = provider.base_url.rstrip("/") + "/chat/completions"
        headers = self._headers(provider, secret)
        messages = [
            {"role": "system", "content": system_message(prompt, task_kind, prompt_version)},
            {"role": "user", "content": prompt.user},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        response = post_json(endpoint, headers, payload, provider.timeout_seconds)
        if response.status_code == 400:
            # 兼容网关常拒绝可选参数（response_format / temperature）；去掉后重试一次，
            # 结构化输出仍由 Pydantic 校验和有限重试兜底。
            logging.getLogger(__name__).info(
                "llm_optional_params_rejected protocol=chat_completions model=%s detail=%s",
                model,
                error_detail(response, secret),
            )
            retry_payload = {"model": model, "messages": messages}
            response = post_json(endpoint, headers, retry_payload, provider.timeout_seconds)
        duration_ms = int((time.perf_counter() - started) * 1000)
        raise_for_status(response, secret)

        parsed, token_usage = self._parse_response(response, schema, secret)
        return parsed, token_usage, duration_ms

    @staticmethod
    def _parse_response(
        response: httpx.Response, schema: type[SchemaT], secret: str
    ) -> tuple[SchemaT, dict[str, int]]:
        """校验协议 envelope 与模型 JSON；失败时留下字段与返回片段供排查。"""

        raw_content = ""
        finish_reason = ""
        try:
            body = ChatResponse.model_validate_json(response.content)
            choice = body.choices[0]
            finish_reason = choice.finish_reason or ""
            raw = choice.message.content
            raw_content = raw if isinstance(raw, str) else "".join(item.text for item in raw)
            parsed = schema.model_validate_json(json_output(raw_content))
        except ValidationError as exc:
            detail = validation_detail(exc, raw_content or response.text, secret, finish_reason)
            raise ExternalServiceError(
                "llm_invalid_output", f"模型返回的结构无法校验：{detail}"
            ) from exc
        token_usage = body.usage.model_dump()
        return parsed, token_usage
