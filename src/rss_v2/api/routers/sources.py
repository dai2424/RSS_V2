"""sources 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.presenters import (
    container,
    health_response,
    source_response,
)
from rss_v2.api.schemas import (
    CollectionRunDetailResponse,
    HealthResponse,
    SourceCreateRequest,
    SourceDeleteResponse,
    SourceDetailResponse,
    SourceListResponse,
    SourcePatchRequest,
    SourceResponse,
    SourceTestResponse,
)
from rss_v2.domain import (
    SourceLanguage,
)

sources_router = APIRouter(prefix="/api/sources", tags=["sources"])


@sources_router.get("", response_model=SourceListResponse)
def list_sources(
    request: Request,
    q: str | None = None,
    category_id: str | None = None,
    enabled: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SourceListResponse:
    service = container(request).source_service
    items: list[SourceResponse] = []
    for item in service.list(q, category_id, enabled, limit, offset):
        response = source_response(item)
        health = service.health_history(item.id, limit=1)
        response.latest_health = health_response(health[0]) if health else None
        response.last_success_at = service.last_success_at(item.id)
        items.append(response)
    return SourceListResponse(items=items, total=service.count(q, category_id, enabled))


@sources_router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreateRequest, request: Request) -> SourceResponse:
    source = container(request).source_service.create(
        payload.name,
        payload.url,
        payload.platform,
        SourceLanguage(payload.language),
        payload.category_id,
        payload.time_offset_minutes,
    )
    return source_response(source)


@sources_router.get("/{source_id}", response_model=SourceDetailResponse)
def get_source(source_id: str, request: Request) -> SourceDetailResponse:
    current = container(request)
    run = current.collection_service.latest_for_source(source_id)
    source = source_response(current.source_service.get(source_id))
    source.last_success_at = current.source_service.last_success_at(source_id)
    return SourceDetailResponse(
        source=source,
        health=[health_response(item) for item in current.source_service.health_history(source_id)],
        message_count=current.message_service.count_for_source(source_id),
        latest_collection=CollectionRunDetailResponse.model_validate(run, from_attributes=True)
        if run
        else None,
    )


@sources_router.delete("/{source_id}", response_model=SourceDeleteResponse)
def delete_source(source_id: str, request: Request) -> SourceDeleteResponse:
    """删除来源及其从属数据；受影响的未完成采集运行按剩余任务重算状态。"""

    current = container(request)
    result = current.source_service.delete(source_id)
    for run_id in result.unfinished_run_ids:
        current.collection_service.refresh_run(run_id)
    return SourceDeleteResponse(deleted_messages=result.messages)


@sources_router.patch("/{source_id}", response_model=SourceResponse)
def update_source(source_id: str, payload: SourcePatchRequest, request: Request) -> SourceResponse:
    changes = payload.model_dump(exclude_unset=True)
    return source_response(container(request).source_service.update(source_id, changes))


@sources_router.post("/{source_id}/test", response_model=SourceTestResponse)
def test_source(source_id: str, request: Request) -> SourceTestResponse:
    source, health = container(request).source_service.test(source_id)
    return SourceTestResponse(source=source_response(source), health=health_response(health))


@sources_router.get("/{source_id}/health", response_model=list[HealthResponse])
def source_health(source_id: str, request: Request) -> list[HealthResponse]:
    return [
        health_response(item)
        for item in container(request).source_service.health_history(source_id)
    ]
