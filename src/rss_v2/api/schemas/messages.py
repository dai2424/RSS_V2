"""消息、译文、加工结果与任务的响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .pagination import Page


class TranslationResponse(BaseModel):
    """翻译结果响应。"""

    id: str
    message_version_id: str
    status: str
    title: str | None
    summary: str | None
    content: str | None
    provider_id: str | None
    key_masked: str | None
    model: str | None
    prompt_version: str
    task_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: int
    updated_at: int


class KeywordResponse(BaseModel):
    """一条检索关键词；text 用于展示，kind 用于检索优先级与界面标注。"""

    text: str
    kind: str


class RelatedMessageResponse(BaseModel):
    """相关消息；shared 是双方共有的关键词，按实体优先排列。"""

    message_id: str
    source_id: str
    version_id: str
    title: str
    published_at: int | None
    collected_at: int
    shared: list[KeywordResponse]


class EnrichmentResponse(BaseModel):
    """内容加工结果响应。"""

    id: str
    message_version_id: str
    status: str
    title: str | None
    summary: str | None
    keywords: list[KeywordResponse]
    provider_id: str | None
    key_masked: str | None
    model: str | None
    prompt_version: str
    task_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: int
    updated_at: int


class MessageVersionResponse(BaseModel):
    """消息版本响应。"""

    id: str
    message_id: str
    version_number: int
    title: str
    summary: str
    content: str
    url: str
    published_at: int | None
    collected_at: int
    language: str
    content_hash: str
    translations: list[TranslationResponse]
    enrichments: list[EnrichmentResponse]
    translation_task: TaskResponse | None = None
    enrichment_task: TaskResponse | None = None


class MessageResponse(BaseModel):
    """消息列表里的一行：消息本体与它的最新版本。"""

    id: str
    source_id: str
    external_id: str
    updated_at: int
    latest_version: MessageVersionResponse | None


class MessageDetailResponse(BaseModel):
    """消息详情响应。"""

    id: str
    source_id: str
    external_id: str
    created_at: int
    updated_at: int
    versions: list[MessageVersionResponse]


class TaskResponse(BaseModel):
    """任务状态响应。

    model / prompt_version 来自非敏感的任务快照；target_* 由任务列表的服务层解析
    （消息标题或来源名），嵌入在消息详情里的任务不填目标，因此给默认值。
    """

    id: str
    task_type: str
    idempotency_key: str
    status: str
    attempts: int
    lease_until: int | None
    input_version_id: str | None
    output_version_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: int
    updated_at: int
    model: str | None = None
    prompt_version: str | None = None
    target_kind: str = ""
    target_id: str | None = None
    target_label: str = ""


class MessageListResponse(Page[MessageResponse]):
    """消息列表响应。"""


class TaskListResponse(Page[TaskResponse]):
    """任务列表响应。"""


class MessageDeletionResponse(BaseModel):
    """删除影响面或删除结果；两种场景共用同一形状。"""

    messages: int
    versions: int
    translations: int
    enrichments: int
    tasks: int


class MessageBulkDeleteRequest(BaseModel):
    """批量删除请求；dry_run 只返回影响面。"""

    message_ids: list[str] = Field(min_length=1, max_length=200)
    dry_run: bool = False


class TaskDeletionResponse(BaseModel):
    """清理任务的结果：命中多少条、实际删除多少条。"""

    candidates: int
    deleted: int


class TaskClearFailedRequest(BaseModel):
    """清理失败任务请求；dry_run 只返回条数。"""

    dry_run: bool = False
