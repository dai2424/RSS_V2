"""选题发现与写作项目路由。

创建接口返回 202 + 批次（与其它任务型接口一致）：真正的处理在 worker 里跑，
界面按批次状态与任务状态轮询；重复点击返回既有批次并标记 reused=true，
不会重复创建在途任务。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.discovery_presenters import (
    discovery_batch_response,
    discovery_detail_response,
    project_detail_response,
    project_response,
)
from rss_v2.api.presenters import container
from rss_v2.api.schemas import (
    DiscoveryBatchDetailResponse,
    DiscoveryBatchListResponse,
    DiscoveryBatchResponse,
    DiscoveryCreateRequest,
    DiscoveryDeletionResponse,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectResponse,
)
from rss_v2.services.discovery import DiscoveryService

discovery_router = APIRouter(prefix="/api/discoveries", tags=["discovery"])
projects_router = APIRouter(prefix="/api/projects", tags=["projects"])


def _service(request: Request) -> DiscoveryService:
    return container(request).discovery_service


@discovery_router.post(
    "", response_model=DiscoveryBatchResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_discovery(request: Request, payload: DiscoveryCreateRequest) -> DiscoveryBatchResponse:
    """发起选题发现：默认最近 7 天、全部订阅；重复请求复用既有批次。"""

    service = _service(request)
    batch, reused = service.create(
        payload.window_start, payload.window_end, payload.category_id, payload.source_id
    )
    return discovery_batch_response(service.batch_view(batch), reused)


@discovery_router.get("", response_model=DiscoveryBatchListResponse)
def list_discoveries(
    request: Request,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(queued|running|succeeded|failed)$",
        description="按批次状态筛选",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> DiscoveryBatchListResponse:
    """批次列表：按创建时间倒序。"""

    service = _service(request)
    batches, total = service.list(limit, offset, status_filter)
    return DiscoveryBatchListResponse(
        items=[discovery_batch_response(service.batch_view(batch)) for batch in batches],
        total=total,
        limit=limit,
        offset=offset,
    )


@discovery_router.get("/{batch_id}", response_model=DiscoveryBatchDetailResponse)
def discovery_detail(batch_id: str, request: Request) -> DiscoveryBatchDetailResponse:
    """批次详情：候选（含成员版本与推荐理由）。"""

    service = _service(request)
    detail = service.detail(batch_id)
    return discovery_detail_response(detail, service.batch_view(detail.batch))


@discovery_router.post(
    "/{batch_id}/candidates/{candidate_id}/select",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def select_candidate(batch_id: str, candidate_id: str, request: Request) -> ProjectResponse:
    """选定候选并建立写作项目；重复选择返回既有项目（幂等）。"""

    service = _service(request)
    project, reused = service.select(batch_id, candidate_id)
    return project_response(project, reused)


@discovery_router.delete("/{batch_id}", response_model=DiscoveryDeletionResponse)
def delete_discovery(batch_id: str, request: Request) -> DiscoveryDeletionResponse:
    """删除批次；候选与无人引用的自动线索一并清理，人工线索不受影响。"""

    return DiscoveryDeletionResponse(deleted=_service(request).delete(batch_id))


@projects_router.get("", response_model=ProjectListResponse)
def list_projects(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ProjectListResponse:
    """写作项目列表：按最近更新时间倒序。"""

    service = _service(request)
    projects, total = service.list_projects(limit, offset, status_filter)
    return ProjectListResponse(
        items=[project_response(project) for project in projects],
        total=total,
        limit=limit,
        offset=offset,
    )


@projects_router.get("/{project_id}", response_model=ProjectDetailResponse)
def project_detail(project_id: str, request: Request) -> ProjectDetailResponse:
    """写作项目详情：本体、成员与各阶段产物数量。"""

    service = _service(request)
    project, members = service.project_detail(project_id)
    return project_detail_response(project, members, service.project_counts(project_id))
