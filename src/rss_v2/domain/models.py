"""RSS v2 的领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceLanguage(StrEnum):
    """来源声明语言。"""

    AUTO = "auto"
    ENGLISH = "en"
    CHINESE = "zh"
    MIXED = "mixed"


class TaskStatus(StrEnum):
    """任务生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskType(StrEnum):
    """第一版支持的任务类型。"""

    COLLECT_SOURCE = "collect_source"
    TRANSLATE_MESSAGE = "translate_message"


@dataclass(frozen=True, slots=True)
class Category:
    """行业分类。"""

    id: str
    name: str
    slug: str
    is_builtin: bool
    is_active: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class Source:
    """RSS 来源基本配置。"""

    id: str
    name: str
    url: str
    platform: str
    language: SourceLanguage
    category_id: str
    enabled: bool
    time_offset_minutes: int
    created_at: int
    updated_at: int
    feed_title: str | None = None
    feed_link: str | None = None
    feed_description: str | None = None
    feed_language: str | None = None
    feed_author: str | None = None
    feed_updated_at: str | None = None
    source_type: str = "rss"
    metadata: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """一次来源健康检查结果。"""

    id: str
    source_id: str
    checked_at: int
    http_status: int | None
    latency_ms: int | None
    parse_success: bool
    entry_count: int
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class FeedItem:
    """解析后的 RSS 条目。"""

    external_id: str
    title: str
    summary: str
    content: str
    url: str
    published_at: str | None
    language: SourceLanguage
    content_hash: str


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    """一次 RSS 请求的 feed 元数据和条目。"""

    title: str | None
    link: str | None
    description: str | None
    language: str | None
    author: str | None
    updated_at: str | None
    metadata: dict[str, Any]
    items: list[FeedItem]


@dataclass(frozen=True, slots=True)
class Message:
    """消息逻辑实体。"""

    id: str
    source_id: str
    external_id: str
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class MessageVersion:
    """不可变的消息内容版本。"""

    id: str
    message_id: str
    version_number: int
    title: str
    summary: str
    content: str
    url: str
    published_at: str | None
    collected_at: int
    language: SourceLanguage
    content_hash: str


@dataclass(frozen=True, slots=True)
class Translation:
    """一条消息版本的中文翻译。"""

    id: str
    message_version_id: str
    status: str
    title: str | None
    summary: str | None
    content: str | None
    provider_id: str | None
    key_ref: str | None
    model: str | None
    prompt_version: str
    task_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class CollectionRun:
    """一次采集运行的汇总。"""

    id: str
    source_ids: list[str]
    status: TaskStatus
    requested_at: int
    completed_at: int | None
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0


@dataclass(frozen=True, slots=True)
class Task:
    """持久化后台任务。"""

    id: str
    task_type: TaskType
    idempotency_key: str
    status: TaskStatus
    attempts: int
    lease_until: int | None
    input_version_id: str | None
    output_version_id: str | None
    payload: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class Provider:
    """LLM provider 的非敏感配置。"""

    id: str
    name: str
    base_url: str
    model: str
    enabled: bool
    priority: int
    timeout_seconds: float
    session_header_name: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class ProviderKey:
    """Provider 下的密钥引用，不包含密钥值。"""

    id: str
    provider_id: str
    key_ref: str
    priority: int
    enabled: bool
    cooldown_until: int | None
    last_status: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """结构化翻译结果。"""

    title: str
    summary: str
    content: str


@dataclass(frozen=True, slots=True)
class LLMCall:
    """模型调用审计信息。"""

    id: str
    task_id: str | None
    provider_id: str
    key_ref: str
    model: str
    prompt_version: str
    input_hash: str
    duration_ms: int
    token_usage: dict[str, int]
    status: str
    error_code: str | None
    created_at: int
