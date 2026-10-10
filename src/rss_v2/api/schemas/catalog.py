"""分类、来源、健康与采集运行的请求和响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pagination import Page


class PatchRequest(BaseModel):
    """PATCH 允许省略字段，但仅会话头允许显式置空。"""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def reject_null(self) -> PatchRequest:
        for key in self.model_fields_set:
            if key != "session_header_name" and getattr(self, key) is None:
                raise ValueError(f"{key} 不能为 null")
        return self


class CategoryCreateRequest(BaseModel):
    """创建分类请求。"""

    name: str = Field(min_length=1, max_length=80)
    slug: str | None = Field(default=None, max_length=80)


class CategoryResponse(BaseModel):
    """分类响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    is_builtin: bool
    is_active: bool
    created_at: int
    updated_at: int


class SourceCreateRequest(BaseModel):
    """创建来源请求。"""

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=2000)
    platform: str = Field(default="", max_length=100)
    language: str = Field(default="auto", pattern="^(auto|en|zh|mixed)$")
    category_id: str = Field(min_length=1)
    time_offset_minutes: int = Field(default=0, ge=-1440, le=1440)


class SourcePatchRequest(PatchRequest):
    """更新来源请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    platform: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, pattern="^(auto|en|zh|mixed)$")
    category_id: str | None = None
    enabled: bool | None = None
    time_offset_minutes: int | None = Field(default=None, ge=-1440, le=1440)


class SourceResponse(BaseModel):
    """来源响应。"""

    id: str
    name: str
    url: str
    platform: str
    language: str
    category_id: str
    enabled: bool
    time_offset_minutes: int
    feed_title: str | None
    feed_link: str | None
    feed_description: str | None
    feed_language: str | None
    feed_author: str | None
    feed_updated_at: str | None
    source_type: str
    metadata: dict[str, Any]
    created_at: int
    updated_at: int
    latest_health: HealthResponse | None = None
    last_success_at: int | None = None


class SourceListResponse(Page[SourceResponse]):
    """来源列表响应。"""


class HealthResponse(BaseModel):
    """来源健康检查响应。"""

    id: str
    source_id: str
    checked_at: int
    http_status: int | None
    latency_ms: int | None
    parse_success: bool
    entry_count: int
    error_code: str | None
    error_message: str | None


class SourceTestResponse(BaseModel):
    """来源测试响应。"""

    source: SourceResponse
    health: HealthResponse


class SourceDetailResponse(BaseModel):
    """来源详情响应。"""

    source: SourceResponse
    health: list[HealthResponse]
    message_count: int = 0
    latest_collection: CollectionRunDetailResponse | None = None


class SourceDeleteResponse(BaseModel):
    """删除来源响应；deleted_messages 为随之删除的消息条数。"""

    deleted_messages: int


class CollectionRunRequest(BaseModel):
    """创建采集运行请求。"""

    source_ids: list[str] = Field(default_factory=list, max_length=100)
    all_enabled: bool = False


class CollectionRunResponse(BaseModel):
    """采集运行响应。"""

    id: str
    source_ids: list[str]
    status: str
    requested_at: int
    task_ids: list[str]


class CollectionRunDetailResponse(BaseModel):
    """采集运行统计。"""

    id: str
    source_ids: list[str]
    status: str
    requested_at: int
    completed_at: int | None
    created_count: int
    updated_count: int
    skipped_count: int
    failed_count: int
