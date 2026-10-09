"""关键词索引与存量回填的请求和响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .messages import KeywordResponse


class KeywordBackfillRequest(BaseModel):
    """存量回填请求；dry_run 只统计候选条数，不入队。"""

    source_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    dry_run: bool = False


class KeywordBackfillResponse(BaseModel):
    """回填结果；reasons 是去重后的跳过原因。"""

    candidates: int
    enqueued: int
    skipped: int
    reasons: list[str]


class KeywordRebuildResponse(BaseModel):
    """关键词索引重建结果：写入的关系表行数。"""

    indexed: int


class KeywordMergeRequest(BaseModel):
    """合并请求；dry_run 只返回影响面，不写别名。"""

    keys: list[str] = Field(min_length=1, max_length=20)
    target: str = Field(min_length=1)
    dry_run: bool = False


class KeywordMergePreviewResponse(BaseModel):
    """合并影响面：合并后的词频、受影响消息数与全部写法。"""

    target: str
    sources: list[str]
    forms: list[KeywordResponse]
    mentions: int
    messages: int


class KeywordMergeRecordResponse(BaseModel):
    """一次合并的记录；已撤销的记录保留供审计。"""

    id: str
    target_key: str
    target_raw: str
    members: list[KeywordResponse]
    messages: int
    created_at: int
    undone_at: int | None


class KeywordMergeResponse(BaseModel):
    """合并结果：影响面，以及执行后的记录（dry_run 时为空）。"""

    record: KeywordMergeRecordResponse | None
    preview: KeywordMergePreviewResponse


class KeywordUndoResponse(BaseModel):
    """撤销结果：恢复的别名行数。"""

    restored: int


class KeywordEntryResponse(BaseModel):
    """词表一行：规范词、展示写法、规模与首末出现时间。"""

    key: str
    text: str
    kind: str
    mentions: int
    sources: int
    first_seen_at: int
    last_seen_at: int
    aliases: list[str]


class KeywordListResponse(BaseModel):
    """词表分页结果。"""

    items: list[KeywordEntryResponse]
    total: int


class KeywordKindStatResponse(BaseModel):
    """某一类型的关键词规模。"""

    kind: str
    terms: int
    mentions: int


class KeywordBucketResponse(BaseModel):
    """长尾分布的一档。"""

    label: str
    terms: int


class KeywordTrendPointResponse(BaseModel):
    """某一天的关键词产出。"""

    day: str
    mentions: int
    new_terms: int


class KeywordSourceStatResponse(BaseModel):
    """来源维度的关键词规模。"""

    source_id: str
    source_name: str
    terms: int
    mentions: int
    top: list[KeywordResponse]


class KeywordOverviewResponse(BaseModel):
    """概览：规模、分布与覆盖；字段含义见 KeywordOverview。"""

    messages: int
    enriched: int
    pending: int
    terms: int
    mentions: int
    singletons: int
    average_per_message: float
    aliases: int
    merges: int
    kinds: list[KeywordKindStatResponse]
    long_tail: list[KeywordBucketResponse]
    trend: list[KeywordTrendPointResponse]
    sources: list[KeywordSourceStatResponse]
    tasks_queued: int
    tasks_running: int
    tasks_succeeded: int
    tasks_failed: int
