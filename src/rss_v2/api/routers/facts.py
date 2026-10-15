"""资料整理路由：发起整理、查看事实与证据、处理缺口与分歧。

事实清单是提纲与全文的输入依据；引文由服务端在资料正文里定位（内容支持关系的
第一层校验），失败或越界的引用在版本计数里可见，不允许只凭"ID 合法"当成有依据。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.fact_presenters import (
    digest_history_response,
    digest_response,
    note_resolution_response,
)
from rss_v2.api.presenters import container, task_response
from rss_v2.api.schemas import (
    DigestHistoryResponse,
    DigestResponse,
    NoteResolutionResponse,
    TaskResponse,
)

facts_router = APIRouter(prefix="/api/projects", tags=["facts"])


def _service(request: Request):
    return container(request).digest_service


@facts_router.post(
    "/{project_id}/facts", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_digest(
    project_id: str,
    request: Request,
    force: bool = Query(default=False, description="重新整理（同一资料包也新起版本）"),
) -> TaskResponse:
    """发起资料整理：把已读资料整理成事实、分歧与缺口。"""

    task, _reused = _service(request).create(project_id, force)
    return task_response(task)


@facts_router.get("/{project_id}/facts", response_model=DigestResponse)
def digest_detail(
    project_id: str,
    request: Request,
    run_id: str | None = Query(default=None, description="指定资料包版本；默认最新"),
) -> DigestResponse:
    """事实、证据、分歧与缺口；默认返回最新版本。"""

    service = _service(request)
    detail = service.detail(project_id, run_id)
    _raw, task_status = service.summary_view(project_id)
    return digest_response(detail, task_status)


@facts_router.get("/{project_id}/digest-runs", response_model=DigestHistoryResponse)
def digest_history(
    project_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> DigestHistoryResponse:
    """历史资料包版本（新到旧），用于查看旧依据。"""

    return digest_history_response(_service(request).runs_for(project_id, limit))


@facts_router.post("/{project_id}/notes/{note_id}/resolve", response_model=NoteResolutionResponse)
def resolve_note(project_id: str, note_id: str, request: Request) -> NoteResolutionResponse:
    """标记缺口/分歧已处理；重复调用幂等。"""

    return note_resolution_response(_service(request).resolve_note(project_id, note_id))
