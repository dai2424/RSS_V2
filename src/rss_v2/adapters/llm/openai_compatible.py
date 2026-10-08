"""OpenAI 兼容模型 Provider。"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, cast

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


USER_AGENT = "rss-v2/0.1"  # 上游要求客户端标识自身而非通用库名


def stable_session_id(provider_id: str) -> str:
    """按 Provider 派生的稳定会话 ID；同一配置跨进程、跨重启保持一致。"""

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rss-v2:{provider_id}"))


class OpenAICompatibleProvider:
    """使用 OpenAI chat completions 协议调用翻译模型。"""

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        endpoint = provider.base_url.rstrip("/") + "/chat/completions"
        prompt = (
            "你是专业科技资讯翻译。请把以下英文 RSS 内容翻译成简洁、准确的简体中文。"
            "必须只返回 JSON 对象，字段严格为 title、summary、content，不要 Markdown 包裹。\n"
            f"标题：{title}\n摘要：{summary}\n正文：{content}"
        )
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
        messages = [
            {"role": "system", "content": f"translation prompt version: {prompt_version}"},
            {"role": "user", "content": prompt},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        response = self._post(endpoint, headers, payload, provider.timeout_seconds)
        if response.status_code == 400:
            # 兼容网关常拒绝可选参数（response_format / temperature）；去掉后重试一次，
            # 结构化输出仍由 Pydantic 校验和有限重试兜底。
            logging.getLogger(__name__).info(
                "llm_optional_params_rejected model=%s detail=%s",
                model,
                self._error_detail(response, secret),
            )
            retry_payload = {"model": model, "messages": messages}
            response = self._post(endpoint, headers, retry_payload, provider.timeout_seconds)
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._raise_for_status(response, secret)

        parsed, token_usage = self._parse_response(response)
        return (
            TranslationResult(parsed.title, parsed.summary, parsed.content),
            token_usage,
            duration_ms,
        )

    @staticmethod
    def _post(
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> httpx.Response:
        """发送单次请求；网络与超时错误在协议边界翻译成稳定分类。"""

        try:
            return httpx.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
        except httpx.TimeoutException as exc:
            raise ExternalServiceError("llm_timeout", "模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError("llm_network_error", "模型网络请求失败") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response, secret: str) -> None:
        """把非 2xx 响应映射为稳定错误分类，并附带脱敏后的上游说明。"""

        status_code = response.status_code
        if status_code < 400:
            return
        detail = OpenAICompatibleProvider._error_detail(response, secret)
        suffix = f"：{detail}" if detail else ""
        if status_code == 429:
            raise ExternalServiceError("rate_limited", f"模型请求频率受限{suffix}")
        if status_code in {401, 403}:
            raise ExternalServiceError(
                "llm_auth_error", f"模型服务认证失败（HTTP {status_code}）{suffix}"
            )
        if status_code >= 500:
            raise ExternalServiceError(
                "llm_server_error", f"模型服务返回 HTTP {status_code}{suffix}"
            )
        raise ExternalServiceError(
            "llm_request_error", f"模型请求被拒绝（HTTP {status_code}）{suffix}"
        )

    @staticmethod
    def _error_detail(response: httpx.Response, secret: str) -> str:
        """读取上游错误说明（error.message / message / detail），脱敏并截断。

        密钥值必须留在适配器之外：即使上游回显请求内容也先替换掉，
        再压成单行并限制长度，避免异常信息进入界面或日志。
        """

        body: object = None
        try:
            body = response.json()
        except ValueError:
            body = None
        detail = ""
        if isinstance(body, dict):
            # isinstance 已限定为 JSON 对象；JSON 解析器保证键是字符串。
            payload = cast(dict[str, Any], body)
            error = payload.get("error")
            if isinstance(error, dict):
                detail = str(cast(dict[str, Any], error).get("message") or "")
                if not detail:
                    detail = str(cast(dict[str, Any], error).get("type") or "")
            elif isinstance(error, str):
                detail = error
            if not detail:
                detail = str(payload.get("message") or payload.get("detail") or "")
        if not detail:
            detail = response.text
        if secret:
            detail = detail.replace(secret, "***")
        detail = " ".join(detail.split())
        return detail[:300]

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
