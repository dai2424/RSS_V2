"""categories 领域路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from rss_v2.api.presenters import (
    category_response,
    container,
)
from rss_v2.api.schemas import (
    CategoryCreateRequest,
    CategoryResponse,
)

categories_router = APIRouter(prefix="/api/categories", tags=["categories"])


@categories_router.get("", response_model=list[CategoryResponse])
def list_categories(request: Request) -> list[CategoryResponse]:
    return [category_response(item) for item in container(request).category_service.list()]


@categories_router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreateRequest, request: Request) -> CategoryResponse:
    return category_response(container(request).category_service.create(payload.name, payload.slug))
