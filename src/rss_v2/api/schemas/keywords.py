"""关键词索引与存量回填的请求和响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .messages import KeywordResponse
from .pagination import Page


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


class KeywordPinRequest(BaseModel):
    """标记或取消常驻词；key 是词表里的规范词键或任意写法，服务端统一归一化。"""

    key: str = Field(min_length=1, max_length=200)
    pinned: bool


class KeywordPinResponse(BaseModel):
    """标记结果；key 是归一化后的规范词键，界面据此更新对应行。"""

    key: str
    pinned: bool


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
    pinned: bool


class KeywordListResponse(Page[KeywordEntryResponse]):
    """词表分页结果。"""


class KeywordSuggestionRequest(BaseModel):
    """排若干批建议任务：count 是批数（默认 1），size 是每批词数（默认取配置）。

    扫描是滚动的：新词优先，老词按最久没看过排队重扫，因此没有"扫完"这个终态。
    """

    count: int = Field(default=1, ge=1, le=50)
    size: int | None = Field(default=None, ge=2, le=100)


class KeywordSuggestionStatusResponse(BaseModel):
    """一次入队的回执与当前进度。"""

    enqueued: int
    queued_words: int
    queued: int
    running: int
    failed: int
    pending: int
    total: int
    scanned: int
    last_scanned_at: int | None


class KeywordSuggestionMemberResponse(BaseModel):
    """建议组里的一个写法：key 用于合并，text 用于展示。"""

    key: str
    text: str
    kind: str


class KeywordSuggestionResponse(BaseModel):
    """一条待确认的合并建议。"""

    id: str
    members: list[KeywordSuggestionMemberResponse]
    target_key: str
    target_text: str
    rationale: str
    status: str
    created_at: int
    resolved_at: int | None
    merge_id: str | None


class KeywordSuggestionListResponse(BaseModel):
    """建议列表与当前进度；界面据此显示"还剩多少词没看"并决定是否轮询。"""

    items: list[KeywordSuggestionResponse]
    pending: int
    queued: int
    running: int
    failed: int
    total: int
    scanned: int
    last_scanned_at: int | None


class KeywordSuggestionAcceptRequest(BaseModel):
    """采纳：带上已经完成的合并记录 id，仅用于标记与追溯。"""

    merge_id: str = Field(min_length=1, max_length=64)


class KeywordPurgeRequest(BaseModel):
    """清理词表状态；dry_run 只数条数。"""

    dry_run: bool = False


class KeywordPurgeResponse(BaseModel):
    """清理结果：扫描记账、合并建议、常驻建议各处理了多少条。"""

    scanned: int
    merge_suggestions: int
    pin_suggestions: int


class PinSuggestionRequest(BaseModel):
    """生成常驻建议；dry_run 只看候选数，不调用模型。"""

    dry_run: bool = False


class PinSuggestionResponse(BaseModel):
    """一条常驻建议：候选词、跨了几周与模型给的理由。"""

    key: str
    text: str
    rationale: str
    weeks: int
    status: str
    created_at: int
    resolved_at: int | None


class PinSuggestionListResponse(BaseModel):
    """常驻建议列表与待处理总数。"""

    items: list[PinSuggestionResponse]
    pending: int


class PinSuggestionBatchResponse(BaseModel):
    """一次生成常驻建议的结果：候选数、落库条数与被丢弃条数。"""

    candidates: int
    picks: int
    dropped: int
    pending: int


class KeywordSuggestionPendingResponse(BaseModel):
    """处置后的待处理数量，界面据此更新提示。"""

    pending: int


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
    hot_days: int
    hot_min_mentions: int
    terms: int
    mentions: int
    singletons: int
    terms_all: int
    mentions_all: int
    singletons_all: int
    average_per_message: float
    aliases: int
    merges: int
    reused: int
    reuse_judged: int
    reuse_rate: float | None
    vocab_revision: int
    pinned: int
    suggestions_pending: int
    kinds: list[KeywordKindStatResponse]
    long_tail: list[KeywordBucketResponse]
    trend: list[KeywordTrendPointResponse]
    sources: list[KeywordSourceStatResponse]
    tasks_queued: int
    tasks_running: int
    tasks_succeeded: int
    tasks_failed: int
