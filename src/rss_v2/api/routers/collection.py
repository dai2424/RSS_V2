"""collection 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from rss_v2.api.presenters import (
    container,
)
from rss_v2.api.schemas import (
    CollectionRunDetailResponse,
    CollectionRunRequest,
    CollectionRunResponse,
)

collection_router = APIRouter(prefix="/api/collection", tags=["collection"])


@collection_router.post(
    "/runs", response_model=CollectionRunResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_collection_run(payload: CollectionRunRequest, request: Request) -> CollectionRunResponse:
    current = container(request)
    source_ids: list[str] = payload.source_ids
    if payload.all_enabled:
        source_ids = []
        offset = 0
        while batch := current.source_service.list(enabled=True, limit=100, offset=offset):
            source_ids.extend(source.id for source in batch)
            offset += len(batch)
    run = current.collection_service.create_run(source_ids)
    task_ids = current.collection_service.task_ids_for_run(run)
    return CollectionRunResponse(
        id=run.id,
        source_ids=run.source_ids,
        status=run.status.value,
        requested_at=run.requested_at,
        task_ids=task_ids,
    )


@collection_router.get("/runs/{run_id}", response_model=CollectionRunDetailResponse)
def get_collection_run(run_id: str, request: Request) -> CollectionRunDetailResponse:
    """读取所有来源汇总后的采集统计。"""
    run = container(request).collection_service.get_run(run_id)
    return CollectionRunDetailResponse.model_validate(run, from_attributes=True)
