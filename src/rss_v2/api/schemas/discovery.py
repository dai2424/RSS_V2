"""选题发现的请求和响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiscoveryCreateRequest(BaseModel):
    """发起一次发现：默认最近 N 天、全部订阅；也可以限定时段与分类/来源。"""

    window_start: int | None = Field(
        default=None, description="窗口起点，UTC 秒（含）；与 window_end 必须同时给出"
    )
    window_end: int | None = Field(
        default=None, description="窗口终点，UTC 秒（不含）；与 window_start 必须同时给出"
    )
    category_id: str | None = Field(default=None, description="限定分类；为空表示全部分类")
    source_id: str | None = Field(default=None, description="限定来源；为空表示全部来源")


class DiscoveryReasonResponse(BaseModel):
    """一条推荐理由：interesting（有趣）/ important（重要）。"""

    kind: str
    text: str


class DiscoveryMemberResponse(BaseModel):
    """候选成员：一条消息版本（事件成员锁定版本，消息后续更新不影响它）。"""

    message_id: str
    version_id: str
    version_number: int
    title: str
    source_id: str
    source_name: str
    published_at: int | None
    collected_at: int


class DiscoveryCandidateResponse(BaseModel):
    """候选事件：概要、时间、成员、推荐理由与已建立的写作项目。"""

    id: str
    rank: int
    title: str
    summary: str
    event_time: int | None
    score: int
    reasons: list[DiscoveryReasonResponse]
    member_count: int
    dropped_members: int
    lead_id: str
    project_id: str | None
    members: list[DiscoveryMemberResponse]


class DiscoveryBatchResponse(BaseModel):
    """发现批次：实际生效范围、状态与处理计数。"""

    id: str
    status: str
    window_start: int
    window_end: int
    category_id: str | None
    category_name: str | None
    source_id: str | None
    source_name: str | None
    message_count: int
    candidate_count: int
    duplicate_members: int
    out_of_range: int
    dropped_members: int
    dropped_candidates: int
    error_code: str | None
    error_message: str | None
    task_id: str | None
    task_status: str | None
    reused: bool
    created_at: int
    updated_at: int


class DiscoveryBatchDetailResponse(BaseModel):
    """批次详情：批次本体 + 候选列表。"""

    batch: DiscoveryBatchResponse
    candidates: list[DiscoveryCandidateResponse]


class DiscoveryBatchListResponse(BaseModel):
    """批次列表。"""

    items: list[DiscoveryBatchResponse]
    total: int
    limit: int
    offset: int


class DiscoveryDeletionResponse(BaseModel):
    """删除批次的结果。"""

    deleted: int


class ProjectResponse(BaseModel):
    """写作项目：选定候选后的写作上下文。"""

    id: str
    title: str
    status: str
    lead_id: str
    candidate_id: str | None
    reused: bool
    created_at: int
    updated_at: int


class ProjectDetailResponse(BaseModel):
    """写作项目详情：本体、成员与各阶段产物数量（后续阶段填充）。"""

    project: ProjectResponse
    members: list[DiscoveryMemberResponse]
    counts: dict[str, int]


class ProjectListResponse(BaseModel):
    """写作项目列表。"""

    items: list[ProjectResponse]
    total: int
    limit: int
    offset: int
