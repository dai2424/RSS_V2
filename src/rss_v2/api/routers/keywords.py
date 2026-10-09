"""keywords 领域路由：索引重建与存量回填。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.presenters import (
    container,
    merge_preview_response,
    merge_record_response,
    merge_response,
)
from rss_v2.api.schemas import (
    KeywordBackfillRequest,
    KeywordBackfillResponse,
    KeywordMergeRecordResponse,
    KeywordMergeRequest,
    KeywordMergeResponse,
    KeywordRebuildResponse,
    KeywordUndoResponse,
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


@keywords_router.get("/merges", response_model=list[KeywordMergeRecordResponse])
def list_merges(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    """最近的合并记录；已撤销的记录也在列表里。"""

    return [
        merge_record_response(item) for item in container(request).keyword_service.merges(limit)
    ]


@keywords_router.post("/merge", response_model=KeywordMergeResponse)
def merge_keywords(request: Request, payload: KeywordMergeRequest) -> KeywordMergeResponse:
    """合并同指写法；dry_run 只返回影响面。"""

    service = container(request).keyword_service
    preview = service.merge_preview(payload.keys, payload.target)
    if payload.dry_run:
        return KeywordMergeResponse(record=None, preview=merge_preview_response(preview))
    return merge_response(service.merge(payload.keys, payload.target), preview)


@keywords_router.post("/merges/{merge_id}/undo", response_model=KeywordUndoResponse)
def undo_merge(merge_id: str, request: Request) -> KeywordUndoResponse:
    """撤销一次合并；别名按原值回填。"""

    return KeywordUndoResponse(restored=container(request).keyword_service.undo_merge(merge_id))
