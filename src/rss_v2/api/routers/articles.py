"""全文路由：按已确认提纲生成、查看版本与受控预览。

全文必须使用用户确认过的那一版提纲；未确认时报 400 `outline_not_confirmed`。
阻塞缺口在这里只做标记不拦生成，由界面显著提示。编辑保存（PUT）与导出属于
P6a/P6b，本路由不提供。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.article_presenters import (
    article_list_response,
    article_preview_response,
    article_version_response,
)
from rss_v2.api.presenters import container, task_response
from rss_v2.api.schemas import (
    ArticleListResponse,
    ArticlePreviewResponse,
    ArticleVersionResponse,
    TaskResponse,
)

articles_router = APIRouter(prefix="/api/projects", tags=["articles"])


def _service(request: Request):
    return container(request).article_service


@articles_router.post(
    "/{project_id}/articles", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_article(
    project_id: str,
    request: Request,
    force: bool = Query(default=False, description="重新生成（同一提纲也新起版本）"),
) -> TaskResponse:
    """发起全文生成：使用项目当前已确认的提纲版本。"""

    task, _reused = _service(request).create(project_id, force)
    return task_response(task)


@articles_router.get("/{project_id}/articles", response_model=ArticleListResponse)
def list_articles(project_id: str, request: Request) -> ArticleListResponse:
    """全文版本列表（新到旧）与项目级当前状态。"""

    return article_list_response(_service(request).view(project_id))


@articles_router.get("/{project_id}/articles/{article_id}", response_model=ArticleVersionResponse)
def get_article(project_id: str, article_id: str, request: Request) -> ArticleVersionResponse:
    """单个版本：正文结构 + Markdown。"""

    service = _service(request)
    version = service.detail(project_id, article_id)
    return article_version_response(version, service.view(project_id).latest_version_id)


@articles_router.get(
    "/{project_id}/articles/{article_id}/preview", response_model=ArticlePreviewResponse
)
def get_article_preview(
    project_id: str, article_id: str, request: Request
) -> ArticlePreviewResponse:
    """受控 HTML 预览 + 引用证据 + 未处理的缺口/分歧。"""

    service = _service(request)
    return article_preview_response(
        service.preview(project_id, article_id), service.view(project_id).task_status
    )
