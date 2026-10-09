"""keywords 领域路由：索引重建与存量回填。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.keyword_presenters import (
    keyword_entry_response,
    keyword_overview_response,
    merge_preview_response,
    merge_record_response,
    merge_response,
)
from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    KeywordBackfillRequest,
    KeywordBackfillResponse,
    KeywordListResponse,
    KeywordMergeRecordResponse,
    KeywordMergeRequest,
    KeywordMergeResponse,
    KeywordOverviewResponse,
    KeywordRebuildResponse,
    KeywordUndoResponse,
)

keywords_router = APIRouter(prefix="/api/keywords", tags=["keywords"])


@keywords_router.get("", response_model=KeywordListResponse)
def list_keywords(
    request: Request,
    q: str | None = None,
    kind: str | None = None,
    min_count: int = Query(default=2, ge=1, le=100, description="最小出现次数，1 表示包含孤词"),
    since: int | None = Query(default=None, description="最近出现时间下界，UTC 秒"),
    until: int | None = Query(default=None, description="最近出现时间上界，UTC 秒"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> KeywordListResponse:
    """词表：按出现次数排序的规范词，默认折叠只出现一次的词。"""

    items, total = container(request).keyword_service.vocabulary(
        q, kind, min_count, since, until, limit, offset
    )
    return KeywordListResponse(items=[keyword_entry_response(item) for item in items], total=total)


@keywords_router.get("/overview", response_model=KeywordOverviewResponse)
def keyword_overview(
    request: Request,
    days: int = Query(default=14, ge=1, le=90, description="趋势天数，含今天"),
    top_sources: int = Query(default=10, ge=1, le=50),
) -> KeywordOverviewResponse:
    """概览：规模、类型构成、长尾、趋势、来源分布与覆盖与回填进度。"""

    return keyword_overview_response(container(request).keyword_service.overview(days, top_sources))


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
