"""合并建议路由：生成一批、看列表、忽略与采纳标记。

生成是请求内的同步调用（与提示词试跑同类）：一次一批、可重复请求，同一批不重复付费。
采纳只做标记，真正的合并走既有 `/api/keywords/merge`，影响面预览与撤销都在那边。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.keyword_presenters import merge_suggestion_response, suggestion_status_response
from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    KeywordSuggestionAcceptRequest,
    KeywordSuggestionListResponse,
    KeywordSuggestionPendingResponse,
    KeywordSuggestionRequest,
    KeywordSuggestionStatusResponse,
)

suggestions_router = APIRouter(prefix="/api/keywords/suggestions", tags=["keywords"])


@suggestions_router.post("", response_model=KeywordSuggestionStatusResponse, status_code=202)
def enqueue_suggestions(
    request: Request, payload: KeywordSuggestionRequest
) -> KeywordSuggestionStatusResponse:
    """把接下来的若干批排成任务；已经生成过或已经排过的批次不重复入队。

    每批一个任务，由独立 worker 执行：进度、耗时与失败重试都在任务页看得见。
    """

    service = container(request).suggestion_service
    result = service.enqueue(payload.count, payload.size)
    return suggestion_status_response(service.status(), result)


@suggestions_router.get("", response_model=KeywordSuggestionListResponse)
def list_suggestions(
    request: Request,
    status: str | None = Query(default="pending", description="pending / accepted / dismissed"),
    limit: int = Query(default=50, ge=1, le=200),
) -> KeywordSuggestionListResponse:
    """建议列表；默认只看待处理的。"""

    service = container(request).suggestion_service
    state = service.status()
    return KeywordSuggestionListResponse(
        items=[merge_suggestion_response(item) for item in service.list(status, limit)],
        pending=state.pending,
        queued=state.queued,
        running=state.running,
        failed=state.failed,
        total=state.total,
        scanned=state.scanned,
    )


@suggestions_router.post(
    "/{suggestion_id}/dismiss", response_model=KeywordSuggestionPendingResponse
)
def dismiss_suggestion(suggestion_id: str, request: Request) -> KeywordSuggestionPendingResponse:
    """忽略一条建议；忽略过的组不会再被建议。"""

    service = container(request).suggestion_service
    service.dismiss(suggestion_id)
    return KeywordSuggestionPendingResponse(pending=service.pending())


@suggestions_router.post("/{suggestion_id}/accept", response_model=KeywordSuggestionPendingResponse)
def accept_suggestion(
    suggestion_id: str, request: Request, payload: KeywordSuggestionAcceptRequest
) -> KeywordSuggestionPendingResponse:
    """标记建议已采纳；合并已在既有端点完成，这里只记下记录 id。"""

    service = container(request).suggestion_service
    service.accept(suggestion_id, payload.merge_id)
    return KeywordSuggestionPendingResponse(pending=service.pending())
