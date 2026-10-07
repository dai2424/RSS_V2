"""tasks 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.presenters import (
    container,
    task_response,
)
from rss_v2.api.schemas import (
    TaskResponse,
)

tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@tasks_router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    return task_response(container(request).task_service.get(task_id))


@tasks_router.get("", response_model=list[TaskResponse])
def list_tasks(
    request: Request,
    status: str | None = Query(default=None, pattern="^(queued|running|succeeded|failed)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TaskResponse]:
    """查询任务列表。"""
    return [
        task_response(task) for task in container(request).task_service.list(status, limit, offset)
    ]


@tasks_router.post("/{task_id}/retry", response_model=TaskResponse, status_code=202)
def retry_task(task_id: str, request: Request) -> TaskResponse:
    """重新排队失败任务。"""
    return task_response(container(request).task_service.retry(task_id))
