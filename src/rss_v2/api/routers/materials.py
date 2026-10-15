"""资料路由：发起资料准备、查看资料快照与缺口。

资料快照是写作的输入证据：读取成功的资料带正文与指纹，失败/未读取的候选也保留
（带错误码），界面据此显示"读到什么、缺什么、为什么缺"。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from rss_v2.api.material_presenters import (
    material_detail_response,
    material_response,
    materials_summary_response,
)
from rss_v2.api.presenters import container, task_response
from rss_v2.api.schemas import (
    MaterialDetailResponse,
    MaterialListResponse,
    TaskResponse,
)

materials_router = APIRouter(prefix="/api/projects", tags=["materials"])


def _service(request: Request):
    return container(request).material_service


@materials_router.post(
    "/{project_id}/materials", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_materials(project_id: str, request: Request) -> TaskResponse:
    """发起资料准备：检索式生成 → 读原文 → 联网补充 → 去重落库。"""

    task, _reused = _service(request).create(project_id)
    return task_response(task)


@materials_router.get("/{project_id}/materials", response_model=MaterialListResponse)
def list_materials(project_id: str, request: Request) -> MaterialListResponse:
    """项目的资料列表、状态计数与上次运行的摘要。"""

    service = _service(request)
    raw_summary, task_status = service.summary_view(project_id)
    return MaterialListResponse(
        items=[material_response(item) for item in service.list(project_id)],
        counts=service.counts(project_id),
        summary=materials_summary_response(raw_summary),
        task_status=task_status,
    )


@materials_router.get(
    "/{project_id}/materials/{material_id}", response_model=MaterialDetailResponse
)
def material_detail(project_id: str, material_id: str, request: Request) -> MaterialDetailResponse:
    """一条资料的完整快照（含正文）。"""

    return material_detail_response(_service(request).detail(project_id, material_id))
