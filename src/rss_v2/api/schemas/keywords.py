"""关键词索引与存量回填的请求和响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
