"""API 请求和响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SourceListResponse(BaseModel):
    """来源列表响应；total 是当前过滤条件下的总数，与 items 的分页窗口无关。"""

    items: list[SourceResponse]
    total: int


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
    translation_task: TaskResponse | None = None


class MessageResponse(BaseModel):
    """消息列表响应。"""

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
    """任务状态响应。"""

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


class ProviderCreateRequest(BaseModel):
    """创建 provider 请求。"""

    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=1000)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    session_header_name: str | None = Field(default=None, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ProviderPatchRequest(PatchRequest):
    """更新 provider 请求。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=8, max_length=1000)
    enabled: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    session_header_name: str | None = Field(default=None, max_length=100)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ConnectionTestRequest(BaseModel):
    """连接测试请求；不填模型时使用第一个启用模型。"""

    model: str | None = Field(default=None, max_length=200)


class ConnectionTestResponse(BaseModel):
    """连接测试响应；只返回密钥掩码，不返回密钥值。"""

    ok: bool
    model: str
    key_masked: str
    latency_ms: int
    total_tokens: int
    error_code: str | None
    error_message: str | None


class ProviderModelCreateRequest(BaseModel):
    """在 provider 下新增模型候选请求。"""

    model: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=100, ge=0, le=10000)


class ProviderModelPatchRequest(PatchRequest):
    """更新模型候选请求。"""

    model: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


class ProviderModelResponse(BaseModel):
    """模型候选响应。"""

    id: str
    provider_id: str
    model: str
    enabled: bool
    priority: int
    created_at: int
    updated_at: int


class ProviderKeyCreateRequest(BaseModel):
    """录入 API Key 请求；密钥值只写入本机数据库。"""

    secret: str = Field(min_length=1, max_length=500)
    priority: int = Field(default=100, ge=0, le=10000)


class ProviderKeyPatchRequest(PatchRequest):
    """更新 API Key 请求。"""

    secret: str | None = Field(default=None, min_length=1, max_length=500)
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class ProviderKeyResponse(BaseModel):
    """API Key 响应；只返回掩码，禁止返回密钥值。"""

    id: str
    provider_id: str
    masked: str
    priority: int
    enabled: bool
    cooldown_until: int | None
    last_status: str | None


class ProviderResponse(BaseModel):
    """provider 响应。"""

    id: str
    name: str
    base_url: str
    enabled: bool
    timeout_seconds: float
    session_header_name: str | None
    keys: list[ProviderKeyResponse]
    models: list[ProviderModelResponse]
    extra_headers: dict[str, str]


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
