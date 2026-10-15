"""线索路由：人工把几条消息组成线索，供后续草稿生成使用。

成员用**消息 id** 传入，服务端取各自当前的最新版本：界面上用户看到的是消息，
不需要知道版本号；锁定版本这件事由服务端完成，详情里返回具体的 version_id。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.lead_presenters import (
    lead_detail_response,
    lead_response,
    lead_summary_response,
)
from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    LeadCreateRequest,
    LeadDeletionResponse,
    LeadDetailResponse,
    LeadListResponse,
    LeadMemberRemovalResponse,
    LeadMessagesRequest,
    LeadMessagesResponse,
    LeadResponse,
)

leads_router = APIRouter(prefix="/api/leads", tags=["leads"])


def _service(request: Request):
    return container(request).lead_service


@leads_router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(request: Request, payload: LeadCreateRequest) -> LeadResponse:
    """新建线索：至少两条消息，成员锁定为各自当前的最新版本。"""

    lead = _service(request).create(payload.title, payload.message_ids, payload.note)
    detail = _service(request).detail(lead.id)
    return lead_response(lead, len(detail.members))


@leads_router.get("", response_model=LeadListResponse)
def list_leads(
    request: Request,
    kind: str = Query(
        default="manual",
        pattern="^(manual|discovery|all)$",
        description="线索来源：manual 人工（默认）、discovery 选题发现、all 全部",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> LeadListResponse:
    """线索列表：按最近成员变化时间倒序；默认只看人工线索。"""

    items, total = _service(request).list(limit, offset, None if kind == "all" else kind)
    return LeadListResponse(
        items=[lead_summary_response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@leads_router.get("/{lead_id}", response_model=LeadDetailResponse)
def lead_detail(lead_id: str, request: Request) -> LeadDetailResponse:
    """线索详情：本体与成员（按加入时间从早到晚）。"""

    return lead_detail_response(_service(request).detail(lead_id))


@leads_router.post("/{lead_id}/messages", response_model=LeadMessagesResponse)
def add_lead_messages(
    lead_id: str, request: Request, payload: LeadMessagesRequest
) -> LeadMessagesResponse:
    """追加成员；已在线索里的消息会被跳过（幂等）。"""

    lead, added = _service(request).add_messages(lead_id, payload.message_ids)
    detail = _service(request).detail(lead.id)
    return LeadMessagesResponse(lead=lead_response(lead, len(detail.members)), added=added)


@leads_router.delete("/{lead_id}/messages/{version_id}", response_model=LeadMemberRemovalResponse)
def remove_lead_member(
    lead_id: str, version_id: str, request: Request
) -> LeadMemberRemovalResponse:
    """移除一个成员（按版本 id）。"""

    lead = _service(request).remove_member(lead_id, version_id)
    detail = _service(request).detail(lead.id)
    return LeadMemberRemovalResponse(lead=lead_response(lead, len(detail.members)), removed=1)


@leads_router.delete("/{lead_id}", response_model=LeadDeletionResponse)
def delete_lead(lead_id: str, request: Request) -> LeadDeletionResponse:
    """删除线索；成员随外键级联删除，消息本身不受影响。"""

    return LeadDeletionResponse(deleted=_service(request).delete(lead_id))
