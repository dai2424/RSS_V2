"""合并建议路由：生成一批、看列表、忽略与采纳标记。

生成是请求内的同步调用（与提示词试跑同类）：一次一批、可重复请求，同一批不重复付费。
采纳只做标记，真正的合并走既有 `/api/keywords/merge`，影响面预览与撤销都在那边。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.keyword_presenters import merge_suggestion_response, suggestion_batch_response
from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    KeywordSuggestionAcceptRequest,
    KeywordSuggestionBatchResponse,
    KeywordSuggestionListResponse,
    KeywordSuggestionPendingResponse,
    KeywordSuggestionRequest,
)

suggestions_router = APIRouter(prefix="/api/keywords/suggestions", tags=["keywords"])


@suggestions_router.post("", response_model=KeywordSuggestionBatchResponse)
def generate_suggestions(
    request: Request, payload: KeywordSuggestionRequest
) -> KeywordSuggestionBatchResponse:
    """把词表的一批交给模型找同指候选；同一批重复请求直接回放。"""

    service = container(request).suggestion_service
    batch = service.generate(payload.offset, payload.size)
    return suggestion_batch_response(batch, service.pending())


@suggestions_router.get("", response_model=KeywordSuggestionListResponse)
def list_suggestions(
    request: Request,
    status: str | None = Query(default="pending", description="pending / accepted / dismissed"),
    limit: int = Query(default=50, ge=1, le=200),
) -> KeywordSuggestionListResponse:
    """建议列表；默认只看待处理的。"""

    service = container(request).suggestion_service
    return KeywordSuggestionListResponse(
        items=[merge_suggestion_response(item) for item in service.list(status, limit)],
        pending=service.pending(),
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
