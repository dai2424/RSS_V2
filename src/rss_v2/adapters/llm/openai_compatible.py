"""OpenAI 兼容模型 Provider。"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rss_v2.domain import EnrichmentResult, ExternalServiceError, Provider, TranslationResult


class TranslationResponse(BaseModel):
    """模型返回的结构化翻译结果。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    summary: str
    content: str


class EnrichmentResponse(BaseModel):
    """模型返回的结构化内容加工结果。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    summary: str
    keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords", mode="before")
    @classmethod
    def _split_keywords(cls, value: object) -> object:
        """模型偶尔把关键词写成顿号或逗号分隔的字符串，这里折算成列表。"""

        if isinstance(value, str):
            return [item for item in re.split(r"[,，、;；]", value)]
        return value


#: 关键词上限：模型偶尔会多给，超出部分对检索没有额外价值，只增加存储与噪声。
KEYWORD_LIMIT = 8


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


USER_AGENT = "rss-v2/0.1"  # 上游要求客户端标识自身而非通用库名

#: 兼容协议的结构化响应类型；按 schema 校验是适配器的职责。
SchemaT = TypeVar("SchemaT", bound=BaseModel)


def stable_session_id(provider_id: str) -> str:
    """按 Provider 派生的稳定会话 ID；同一配置跨进程、跨重启保持一致。"""

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rss-v2:{provider_id}"))


def _keywords(values: list[str]) -> tuple[str, ...]:
    """去空白、去重并截断关键词；顺序按模型给出的重要性保留。"""

    seen: dict[str, None] = {}
    for value in values:
        keyword = " ".join(value.split())
        if keyword:
            seen.setdefault(keyword, None)
    return tuple(list(seen)[:KEYWORD_LIMIT])


def _redact(text: str, secret: str, limit: int = 300) -> str:
    """压成单行、替换密钥并限制长度；异常信息进入界面或日志前必须经过它。"""

    if secret:
        text = text.replace(secret, "***")
    return " ".join(text.split())[:limit]


def _json_object(raw: str) -> str:
    """截取模型输出里的 JSON 对象。

    推理模型和部分网关会在 JSON 前后加说明文字或 Markdown 围栏，直接解析会失败；
    这里只取首尾大括号之间的内容。截取不到时原样返回，仍由 schema 校验兜底。
    """

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return raw
    return raw[start : end + 1]


def _validation_detail(error: ValidationError, raw: str, secret: str, finish_reason: str) -> str:
    """结构化输出失败的可见说明：失败字段、结束原因和返回片段。"""

    errors = error.errors()
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first["loc"]) or "根"
        reason = f"{location}（{first['type']}）"
    else:
        reason = "未提供字段信息"
    if finish_reason and finish_reason != "stop":
        reason = f"{reason}，结束原因 {finish_reason}"
    return f"{reason}；返回片段：{_redact(raw, secret, 200)}"


class OpenAICompatibleProvider:
    """使用 OpenAI chat completions 协议调用翻译与内容加工模型。"""

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
        prompt = (
            "你是专业科技资讯翻译。请把以下英文 RSS 内容翻译成简洁、准确的简体中文。"
            "必须只返回 JSON 对象，字段严格为 title、summary、content，不要 Markdown 包裹。\n"
            f"标题：{title}\n摘要：{summary}\n正文：{content}"
        )
        parsed, tokens, duration = self._complete(
            provider,
            model,
            secret,
            f"translation prompt version: {prompt_version}",
            prompt,
            TranslationResponse,
        )
        return TranslationResult(parsed.title, parsed.summary, parsed.content), tokens, duration

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]:
        prompt = (
            "你是科技资讯编辑。请阅读以下 RSS 内容，用简体中文产出便于浏览和检索的结果：\n"
            "title 是精简标题，不超过 40 字，保留关键主体，不添加原文没有的信息；\n"
            "summary 是 1 至 3 句摘要，说明发生了什么；\n"
            "keywords 是 3 至 8 个检索关键词，可以是中文词或原文专有名词。\n"
            "必须只返回 JSON 对象，字段严格为 title、summary、keywords（字符串数组），"
            "不要 Markdown 包裹。\n"
            f"标题：{title}\n摘要：{summary}\n正文：{content}"
        )
        parsed, tokens, duration = self._complete(
            provider,
            model,
            secret,
            f"enrichment prompt version: {prompt_version}",
            prompt,
            EnrichmentResponse,
        )
        result = EnrichmentResult(parsed.title, parsed.summary, _keywords(parsed.keywords))
        return result, tokens, duration

    def _complete(
        self,
        provider: Provider,
        model: str,
        secret: str,
        system_prompt: str,
        prompt: str,
        schema: type[SchemaT],
    ) -> tuple[SchemaT, dict[str, int], int]:
        """发送一次结构化请求并校验响应；翻译与加工共用同一协议边界。"""

        endpoint = provider.base_url.rstrip("/") + "/chat/completions"
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
            {"role": "system", "content": system_prompt},
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

        parsed, token_usage = self._parse_response(response, schema, secret)
        return parsed, token_usage, duration_ms

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
        return _redact(detail, secret)

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
            parsed = schema.model_validate_json(_json_object(raw_content))
        except ValidationError as exc:
            detail = _validation_detail(exc, raw_content or response.text, secret, finish_reason)
            raise ExternalServiceError(
                "llm_invalid_output", f"模型返回的结构无法校验：{detail}"
            ) from exc
        token_usage = body.usage.model_dump()
        return parsed, token_usage
