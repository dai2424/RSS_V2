"""常驻词建议路由：跨周候选 → 模型挑选 → 人工确认。

候选由系统按"最近 4 周里至少 3 周出现、且本周仍在热词窗口内"算出（可复算），
模型只从中挑选并给理由；采纳才写入常驻表。候选只有几十个词，因此是请求内的同步调用，
不进任务队列——与提示词试跑同类。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.keyword_presenters import (
    pin_suggestion_batch_response,
    pin_suggestion_list_response,
)
from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    KeywordSuggestionPendingResponse,
    PinSuggestionBatchResponse,
    PinSuggestionListResponse,
    PinSuggestionRequest,
)

pin_suggestions_router = APIRouter(prefix="/api/keywords/pin-suggestions", tags=["keywords"])


@pin_suggestions_router.post("", response_model=PinSuggestionBatchResponse)
def suggest_pins(request: Request, payload: PinSuggestionRequest) -> PinSuggestionBatchResponse:
    """从跨周候选里挑出值得常驻的词；候选为空（样本不够）时不调用模型。"""

    service = container(request).suggestion_service
    batch = service.suggest_pins(payload.dry_run)
    return pin_suggestion_batch_response(batch, service.pending())


@pin_suggestions_router.get("", response_model=PinSuggestionListResponse)
def list_pin_suggestions(
    request: Request,
    status: str | None = Query(default="pending", description="pending / accepted / dismissed"),
    limit: int = Query(default=50, ge=1, le=200),
) -> PinSuggestionListResponse:
    """常驻建议列表；默认只看待处理的。"""

    service = container(request).suggestion_service
    return pin_suggestion_list_response(service.pin_suggestions(status, limit), service.pending())


@pin_suggestions_router.post("/{key}/accept", response_model=KeywordSuggestionPendingResponse)
def accept_pin_suggestion(key: str, request: Request) -> KeywordSuggestionPendingResponse:
    """采纳常驻建议：写入常驻表，该词从此在注入时必然出现。"""

    service = container(request).suggestion_service
    service.resolve_pin(key, True)
    return KeywordSuggestionPendingResponse(pending=service.pending())


@pin_suggestions_router.post("/{key}/dismiss", response_model=KeywordSuggestionPendingResponse)
def dismiss_pin_suggestion(key: str, request: Request) -> KeywordSuggestionPendingResponse:
    """忽略常驻建议；忽略过的词不会再被建议。"""

    service = container(request).suggestion_service
    service.resolve_pin(key, False)
    return KeywordSuggestionPendingResponse(pending=service.pending())
