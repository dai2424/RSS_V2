"""messages 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.presenters import (
    container,
    task_response,
    version_response,
)
from rss_v2.api.schemas import (
    MessageDetailResponse,
    MessageResponse,
    TaskResponse,
)

messages_router = APIRouter(prefix="/api/messages", tags=["messages"])


@messages_router.get("", response_model=list[MessageResponse])
def list_messages(
    request: Request,
    q: str | None = None,
    source_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[MessageResponse]:
    values: list[MessageResponse] = []
    for message, version, translations in container(request).message_service.list(
        q, source_id, limit, offset
    ):
        values.append(
            MessageResponse(
                id=message.id,
                source_id=message.source_id,
                external_id=message.external_id,
                updated_at=message.updated_at,
                latest_version=version_response(
                    version, translations, container(request).task_service.for_version(version.id)
                )
                if version
                else None,
            )
        )
    return values


@messages_router.get("/{message_id}", response_model=MessageDetailResponse)
def get_message(message_id: str, request: Request) -> MessageDetailResponse:
    message, versions = container(request).message_service.detail(message_id)
    return MessageDetailResponse(
        id=message.id,
        source_id=message.source_id,
        external_id=message.external_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        versions=[
            version_response(
                version, translations, container(request).task_service.for_version(version.id)
            )
            for version, translations in versions
        ],
    )


@messages_router.post(
    "/{message_id}/translate", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def translate_message(message_id: str, request: Request) -> TaskResponse:
    return task_response(container(request).translation_service.create_task(message_id))
