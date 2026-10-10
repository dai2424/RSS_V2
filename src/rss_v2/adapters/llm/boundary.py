"""模型协议边界的共用件。

OpenAI 兼容 chat completions 与 Anthropic Messages 只在端点、认证头、请求体形状
和响应包装上不同；结构化输出 schema、错误分类、密钥脱敏与 JSON 截取必须只有一份
实现，否则同一类上游错误会在两种协议里被分类成不同的错误码。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any, TypeVar, cast

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from rss_v2.domain import (
    ExternalServiceError,
    Keyword,
    KeywordKind,
    normalize_keyword_list,
)
from rss_v2.llm import PromptPayload


class TranslationResponse(BaseModel):
    """模型返回的结构化翻译结果。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    summary: str
    content: str


def _single_item(value: object) -> object | None:
    """单元素数组取其唯一元素；其它形状返回 None。

    独立成函数是为了让类型收敛：直接在验证器里 isinstance 判断会留下
    部分未知的列表类型，Pyright strict 下无法通过。
    """

    if not isinstance(value, (list, tuple)):
        return None
    items = list(cast(Sequence[object], value))
    return items[0] if len(items) == 1 else None


class KeywordItem(BaseModel):
    """模型返回的单条关键词。

    这里刻意容忍几种"形状打滑"：多余字段忽略、字段名换成 keyword/value/name、
    text 误写成单元素数组、kind 写成数字或 null。它们都不影响这条关键词的语义，
    为一次形状失误作废整轮输出（还要重试三次）不划算。
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    text: str = Field(
        min_length=1, validation_alias=AliasChoices("text", "keyword", "value", "name")
    )
    kind: str = "topic"

    @field_validator("text", mode="before")
    @classmethod
    def _unwrap_text(cls, value: object) -> object:
        """模型偶尔把关键词包在单元素数组里。"""

        unwrapped = _single_item(value)
        return value if unwrapped is None else unwrapped

    @field_validator("kind", mode="before")
    @classmethod
    def _stringify_kind(cls, value: object) -> object:
        """kind 写成数字或 null 时按主题词处理，不让整条结果失败。"""

        if value is None:
            return "topic"
        if isinstance(value, (int, float)):
            return str(value)
        return value


class EnrichmentResponse(BaseModel):
    """模型返回的结构化内容加工结果。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    summary: str
    keywords: list[str | KeywordItem] = Field(default_factory=list[str | KeywordItem])

    @field_validator("keywords", mode="before")
    @classmethod
    def _split_keywords(cls, value: object) -> object:
        """模型偶尔把关键词写成顿号或逗号分隔的字符串，这里折算成列表。"""

        if isinstance(value, str):
            return [item for item in re.split(r"[,，、;；]", value)]
        return value


USER_AGENT = "rss-v2/0.1"  # 上游要求客户端标识自身而非通用库名

#: 结构化响应类型；按 schema 校验是适配器的职责。
SchemaT = TypeVar("SchemaT", bound=BaseModel)

#: 探测请求的标记：不属于任何业务任务，日志与假端点据此认出连接测试。
PROBE_TASK = "probe"
PROBE_VERSION = "connection-test"

#: 回复片段上限：连接测试只把模型的一句话带回界面，不需要完整正文。
REPLY_LIMIT = 120


def reply_snippet(text: str) -> str:
    """压成单行并截断，作为连接测试的结果展示。"""

    return " ".join(text.split())[:REPLY_LIMIT]


def parse_envelope[T: BaseModel](response: httpx.Response, model: type[T], secret: str) -> T:
    """只校验协议 envelope，不校验业务结构。

    连接测试用它判断"上游是否按协议回话"；失败时给出脱敏片段，
    上层据此区分"连不通"与"连得通但输出不合业务 schema"。
    """

    try:
        return model.model_validate_json(response.content)
    except ValidationError as exc:
        detail = validation_detail(exc, response.text, secret, "")
        raise ExternalServiceError("llm_invalid_output", f"模型响应不符合协议：{detail}") from exc


def keywords(values: list[str | KeywordItem]) -> tuple[Keyword, ...]:
    """把模型输出折算成领域关键词：纯字符串与非法类型都按主题词处理。

    类型只认 entity/topic/event；模型给出别的取值时降级为主题词，而不是让整次调用失败——
    这个词本身可能仍然有用，为它作废整轮输出不划算。
    """

    items: list[Keyword] = []
    for value in values:
        raw = value if isinstance(value, str) else value.text
        kind_text = KeywordKind.TOPIC.value if isinstance(value, str) else value.kind
        try:
            kind = KeywordKind(kind_text.strip().casefold())
        except ValueError:
            kind = KeywordKind.TOPIC
        items.append(Keyword(text=raw, kind=kind))
    return normalize_keyword_list(items)


def stable_session_id(provider_id: str) -> str:
    """按 Provider 派生的稳定会话 ID；同一配置跨进程、跨重启保持一致。"""

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rss-v2:{provider_id}"))


def system_message(prompt: PromptPayload, task_kind: str, prompt_version: str) -> str:
    """系统消息 = 适配器标记 + 用户编写的系统提示。

    标记是协议层的稳定标识，供网关、假端点与日志识别任务类型与提示词版本；
    它不属于用户可编辑内容，也不随模板变化。
    """

    marker = f"[task: {task_kind}, prompt: {prompt_version}]"
    return f"{marker}\n{prompt.system}" if prompt.system.strip() else marker


def redact(text: str, secret: str, limit: int = 300) -> str:
    """压成单行、替换密钥并限制长度；异常信息进入界面或日志前必须经过它。"""

    if secret:
        text = text.replace(secret, "***")
    return " ".join(text.split())[:limit]


def json_output(raw: str) -> str:
    """截取模型输出里的 JSON 对象。

    推理模型和部分网关会在 JSON 前后加说明文字或 Markdown 围栏，直接解析会失败；
    这里只取首尾大括号之间的内容。截取不到时原样返回，仍由 schema 校验兜底。
    """

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return raw
    return raw[start : end + 1]


#: 联合类型的分支标记；定位信息里去掉它们，否则会显示成 keywords.0.str.kind。
_UNION_TAGS = frozenset({"str", "int", "float", "bool", "None", "KeywordItem"})


def validation_detail(error: ValidationError, raw: str, secret: str, finish_reason: str) -> str:
    """结构化输出失败的可见说明：失败字段、结束原因和返回片段。

    定位取**最具体**的那条错误：联合类型（例如 str | KeywordItem）失败时，Pydantic 会
    同时给出"不是字符串"和"对象里哪个字段不对"两条，只看第一条会一直显示
    keywords.0（string_type）这种没有信息量的结论。

    finish_reason 传协议里"正常结束"以外的取值：OpenAI 用 stop、Anthropic 用
    end_turn，调用方负责归一化，这里只关心"是不是被截断"。
    """

    errors = error.errors()
    if errors:
        picked = max(errors, key=lambda item: len(item["loc"]))
        parts = [str(part) for part in picked["loc"] if str(part) not in _UNION_TAGS]
        location = ".".join(parts) or "根"
        reason = f"{location}（{picked['type']}）"
    else:
        reason = "未提供字段信息"
    if finish_reason and finish_reason != "stop":
        reason = f"{reason}，结束原因 {finish_reason}"
    return f"{reason}；返回片段：{redact(raw, secret, 200)}"


def post_json(
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


def raise_for_status(response: httpx.Response, secret: str) -> None:
    """把非 2xx 响应映射为稳定错误分类，并附带脱敏后的上游说明。"""

    status_code = response.status_code
    if status_code < 400:
        return
    detail = error_detail(response, secret)
    suffix = f"：{detail}" if detail else ""
    if status_code == 429:
        raise ExternalServiceError("rate_limited", f"模型请求频率受限{suffix}")
    if status_code in {401, 403}:
        raise ExternalServiceError(
            "llm_auth_error", f"模型服务认证失败（HTTP {status_code}）{suffix}"
        )
    if status_code >= 500:
        raise ExternalServiceError("llm_server_error", f"模型服务返回 HTTP {status_code}{suffix}")
    raise ExternalServiceError("llm_request_error", f"模型请求被拒绝（HTTP {status_code}）{suffix}")


def error_detail(response: httpx.Response, secret: str) -> str:
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
    return redact(detail, secret)
