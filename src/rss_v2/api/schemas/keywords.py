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
