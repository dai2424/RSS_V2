"""资料准备的请求与响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class MaterialResponse(BaseModel):
    """一条资料（列表里不含正文）。"""

    id: str
    project_id: str
    kind: str  # rss / web
    status: str  # read / summary_only / unavailable / failed
    url: str
    final_url: str
    title: str
    publisher: str
    published_at: int | None
    fetched_at: int
    content_hash: str
    byte_size: int
    truncated: bool
    message_version_id: str | None
    error_code: str
    error_message: str
    created_at: int


class MaterialDetailResponse(MaterialResponse):
    """资料详情：带正文快照（界面按需展开）。"""

    content_text: str


class MaterialQueryResponse(BaseModel):
    """一条检索式的执行结果：命中数、未响应引擎与失败分类。"""

    query: str
    purpose: str
    hits: int
    unresponsive_engines: list[str]
    error_code: str


class MaterialsSummaryResponse(BaseModel):
    """资料准备摘要：范围、计数与缺口说明。"""

    queries: list[MaterialQueryResponse]
    read: int
    summary_only: int
    unavailable: int
    failed: int
    duplicates: int
    skipped_by_budget: int
    notes: list[str]
    task_id: str | None
    finished_at: int


class MaterialListResponse(BaseModel):
    """项目资料列表：条目、状态计数、上次摘要与在途任务状态。"""

    items: list[MaterialResponse]
    counts: dict[str, int]
    summary: MaterialsSummaryResponse
    task_status: str | None
