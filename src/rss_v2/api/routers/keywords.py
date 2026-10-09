"""keywords 领域路由：索引重建与存量回填。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    KeywordBackfillRequest,
    KeywordBackfillResponse,
    KeywordRebuildResponse,
)

keywords_router = APIRouter(prefix="/api/keywords", tags=["keywords"])


@keywords_router.post("/rebuild", response_model=KeywordRebuildResponse)
def rebuild_index(request: Request) -> KeywordRebuildResponse:
    """按已存的加工结果重建关键词索引；不调用模型，可重复执行。"""

    return KeywordRebuildResponse(indexed=container(request).keyword_service.rebuild_index())


@keywords_router.post("/backfill", response_model=KeywordBackfillResponse)
def backfill_keywords(request: Request, payload: KeywordBackfillRequest) -> KeywordBackfillResponse:
    """为没有加工结果的存量消息入队；先用 dry_run 看条数再执行。"""

    result = container(request).keyword_service.backfill(
        payload.source_id, payload.limit, payload.dry_run
    )
    return KeywordBackfillResponse(
        candidates=result.candidates,
        enqueued=result.enqueued,
        skipped=result.skipped,
        reasons=list(result.reasons),
    )
