"""提纲路由：生成、查看版本、人工保存与确认。

提纲是"写什么、按什么顺序写、每节依据哪些已核对事实"的契约：确认某个版本之后才
具备生成全文的资格（P5）。阻塞缺口在这里只做标记不拦确认，是否拒绝生成全文留给 P5。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.outline_presenters import (
    outline_draft,
    outline_list_response,
    outline_response,
)
from rss_v2.api.presenters import container, task_response
from rss_v2.api.schemas import (
    OutlineListResponse,
    OutlineResponse,
    OutlineSaveRequest,
    TaskResponse,
)

outlines_router = APIRouter(prefix="/api/projects", tags=["outlines"])


def _service(request: Request):
    return container(request).outline_service


@outlines_router.post(
    "/{project_id}/outlines", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_outline(
    project_id: str,
    request: Request,
    force: bool = Query(default=False, description="重新生成（同一资料包也新起版本）"),
) -> TaskResponse:
    """发起提纲生成：依据项目最新的资料整理版本生成一版提纲草稿。"""

    task, _reused = _service(request).create(project_id, force)
    return task_response(task)


@outlines_router.get("/{project_id}/outlines", response_model=OutlineListResponse)
def list_outlines(project_id: str, request: Request) -> OutlineListResponse:
    """提纲版本列表（新到旧）与项目级当前状态。"""

    return outline_list_response(_service(request).view(project_id))


@outlines_router.put(
    "/{project_id}/outlines/{outline_id}",
    response_model=OutlineResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_outline(
    project_id: str,
    outline_id: str,
    payload: OutlineSaveRequest,
    request: Request,
) -> OutlineResponse:
    """基于某一版保存人工稿：另存新版本（draft），不改写被改的那一版。

    路径里的 `outline_id` 是被改的基准版本，返回体里的是新版本——保存产生新资源，
    因此用 201（与其它"创建"接口一致）。
    """

    service = _service(request)
    version = service.save_human(project_id, outline_id, outline_draft(payload))
    return outline_response(version, service.view(project_id))


@outlines_router.post("/{project_id}/outlines/{outline_id}/confirm", response_model=OutlineResponse)
def confirm_outline(project_id: str, outline_id: str, request: Request) -> OutlineResponse:
    """确认提纲版本：只有最新版本可确认，重复确认幂等。"""

    service = _service(request)
    version = service.confirm(project_id, outline_id)
    return outline_response(version, service.view(project_id))
