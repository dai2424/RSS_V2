"""messages 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.keyword_presenters import related_message_response
from rss_v2.api.presenters import (
    container,
    task_response,
    version_response,
)
from rss_v2.api.schemas import (
    MessageDetailResponse,
    MessageResponse,
    MessageVersionResponse,
    RelatedMessageResponse,
    TaskResponse,
)
from rss_v2.domain import TaskType
from rss_v2.services.messages import MessageRow

messages_router = APIRouter(prefix="/api/messages", tags=["messages"])


def _version_response(request: Request, row: MessageRow) -> MessageVersionResponse | None:
    """把消息行转成响应；翻译与加工任务状态分别读取。"""
    version = row.version
    if version is None:
        return None
    tasks = container(request).task_service
    return version_response(
        version,
        row.translations,
        row.enrichments,
        tasks.for_version(version.id, TaskType.TRANSLATE_MESSAGE),
        tasks.for_version(version.id, TaskType.ENRICH_MESSAGE),
    )


@messages_router.get("", response_model=list[MessageResponse])
def list_messages(
    request: Request,
    q: str | None = None,
    source_id: str | None = None,
    keyword: str | None = None,
    kind: str | None = None,
    since: int | None = Query(default=None, description="发布时间下界，UTC 秒"),
    until: int | None = Query(default=None, description="发布时间上界，UTC 秒"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[MessageResponse]:
    values: list[MessageResponse] = []
    for row in container(request).message_service.list(
        q, source_id, keyword, kind, since, until, limit, offset
    ):
        values.append(
            MessageResponse(
                id=row.message.id,
                source_id=row.message.source_id,
                external_id=row.message.external_id,
                updated_at=row.message.updated_at,
                latest_version=_version_response(request, row),
            )
        )
    return values


@messages_router.get("/{message_id}", response_model=MessageDetailResponse)
def get_message(message_id: str, request: Request) -> MessageDetailResponse:
    message, rows = container(request).message_service.detail(message_id)
    versions = [_version_response(request, row) for row in rows]
    return MessageDetailResponse(
        id=message.id,
        source_id=message.source_id,
        external_id=message.external_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        versions=[item for item in versions if item is not None],
    )


@messages_router.get("/{message_id}/related", response_model=list[RelatedMessageResponse])
def related_messages(
    message_id: str,
    request: Request,
    limit: int = Query(default=8, ge=1, le=20),
) -> list[RelatedMessageResponse]:
    """共享关键词的其他消息；没有关键词时返回空列表。"""

    return [
        related_message_response(item)
        for item in container(request).keyword_service.related(message_id, limit)
    ]


@messages_router.post(
    "/{message_id}/translate", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def translate_message(message_id: str, request: Request) -> TaskResponse:
    return task_response(container(request).translation_service.create_task(message_id))


@messages_router.post(
    "/{message_id}/enrich", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def enrich_message(message_id: str, request: Request) -> TaskResponse:
    return task_response(container(request).enrichment_service.create_task(message_id))
