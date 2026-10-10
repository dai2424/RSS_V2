"""tasks 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from rss_v2.api.presenters import (
    container,
    task_deletion_response,
    task_view_response,
)
from rss_v2.api.schemas import (
    TaskClearRequest,
    TaskDeletionResponse,
    TaskListResponse,
    TaskResponse,
)

tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@tasks_router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    service = container(request).task_service
    return task_view_response(service.view(service.get(task_id)))


@tasks_router.get("", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    status: str | None = Query(default=None, pattern="^(queued|running|succeeded|failed)$"),
    task_type: str | None = Query(
        default=None,
        pattern="^(collect_source|translate_message|enrich_message)$",
        description="任务类型：采集、翻译或内容加工",
    ),
    source_id: str | None = Query(
        default=None, description="来源：采集任务看任务快照，消息任务看所属来源"
    ),
    q: str | None = Query(default=None, description="按消息标题或来源名搜索"),
    since: int | None = Query(default=None, description="创建时间下界，UTC 秒"),
    until: int | None = Query(default=None, description="创建时间上界，UTC 秒"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TaskListResponse:
    """查询任务列表。"""
    service = container(request).task_service
    return TaskListResponse(
        items=[
            task_view_response(view)
            for view in service.list_views(
                status, task_type, source_id, q, since, until, limit, offset
            )
        ],
        total=service.count(status, task_type, source_id, q, since, until),
    )


@tasks_router.post("/{task_id}/retry", response_model=TaskResponse, status_code=202)
def retry_task(task_id: str, request: Request) -> TaskResponse:
    """重新排队失败任务。"""
    service = container(request).task_service
    return task_view_response(service.view(service.retry(task_id)))


@tasks_router.post("/clear", response_model=TaskDeletionResponse)
def clear_tasks(request: Request, payload: TaskClearRequest) -> TaskDeletionResponse:
    """清理指定终态的任务；dry_run 先看条数。"""

    return task_deletion_response(
        container(request).task_service.clear_finished(payload.statuses, payload.dry_run)
    )


@tasks_router.delete("/{task_id}", response_model=TaskDeletionResponse)
def delete_task(task_id: str, request: Request) -> TaskDeletionResponse:
    """删除已结束的任务；排队与运行中的任务拒绝删除。"""

    return task_deletion_response(container(request).task_service.delete(task_id))
