"""按领域拆分的 FastAPI 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, status

from rss_v2.api.schemas import (
    CategoryCreateRequest,
    CategoryResponse,
    CollectionRunRequest,
    CollectionRunResponse,
    HealthResponse,
    MessageDetailResponse,
    MessageResponse,
    MessageVersionResponse,
    ProviderCreateRequest,
    ProviderKeyCreateRequest,
    ProviderKeyPatchRequest,
    ProviderKeyResponse,
    ProviderPatchRequest,
    ProviderResponse,
    SourceCreateRequest,
    SourcePatchRequest,
    SourceResponse,
    SourceTestResponse,
    TaskResponse,
    TranslationResponse,
)
from rss_v2.bootstrap import Container
from rss_v2.domain import (
    Category,
    HealthCheck,
    MessageVersion,
    Provider,
    ProviderKey,
    Source,
    SourceLanguage,
    Task,
    Translation,
)


def container(request: Request) -> Container:
    """获取由应用组合根装配的容器。"""

    return request.app.state.container


def category_response(category: Category) -> CategoryResponse:
    return CategoryResponse.model_validate(category, from_attributes=True)


def source_response(source: Source) -> SourceResponse:
    return SourceResponse(
        id=source.id,
        name=source.name,
        url=source.url,
        platform=source.platform,
        language=source.language.value,
        category_id=source.category_id,
        enabled=source.enabled,
        time_offset_minutes=source.time_offset_minutes,
        feed_title=source.feed_title,
        feed_link=source.feed_link,
        feed_description=source.feed_description,
        feed_language=source.feed_language,
        feed_author=source.feed_author,
        feed_updated_at=source.feed_updated_at,
        source_type=source.source_type,
        metadata=source.metadata,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def health_response(health: HealthCheck) -> HealthResponse:
    return HealthResponse.model_validate(health, from_attributes=True)


def translation_response(translation: Translation) -> TranslationResponse:
    return TranslationResponse.model_validate(translation, from_attributes=True)


def version_response(
    version: MessageVersion, translations: list[Translation]
) -> MessageVersionResponse:
    return MessageVersionResponse(
        id=version.id,
        message_id=version.message_id,
        version_number=version.version_number,
        title=version.title,
        summary=version.summary,
        content=version.content,
        url=version.url,
        published_at=version.published_at,
        collected_at=version.collected_at,
        language=version.language.value,
        content_hash=version.content_hash,
        translations=[translation_response(item) for item in translations],
    )


def task_response(task: Task) -> TaskResponse:
    return TaskResponse.model_validate(task, from_attributes=True)


def provider_key_response(key: ProviderKey) -> ProviderKeyResponse:
    return ProviderKeyResponse(
        id=key.id,
        provider_id=key.provider_id,
        key_ref=key.key_ref,
        priority=key.priority,
        enabled=key.enabled,
        cooldown_until=key.cooldown_until,
        last_status=key.last_status,
    )


def provider_response(provider: Provider, keys: list[ProviderKey]) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        model=provider.model,
        enabled=provider.enabled,
        priority=provider.priority,
        timeout_seconds=provider.timeout_seconds,
        session_header_name=provider.session_header_name,
        keys=[provider_key_response(key) for key in keys],
    )


categories_router = APIRouter(prefix="/api/categories", tags=["categories"])
sources_router = APIRouter(prefix="/api/sources", tags=["sources"])
collection_router = APIRouter(prefix="/api/collection", tags=["collection"])
messages_router = APIRouter(prefix="/api/messages", tags=["messages"])
tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])
providers_router = APIRouter(prefix="/api/llm/providers", tags=["llm"])


@categories_router.get("", response_model=list[CategoryResponse])
def list_categories(request: Request) -> list[CategoryResponse]:
    return [category_response(item) for item in container(request).category_service.list()]


@categories_router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreateRequest, request: Request) -> CategoryResponse:
    return category_response(container(request).category_service.create(payload.name, payload.slug))


@sources_router.get("", response_model=list[SourceResponse])
def list_sources(
    request: Request,
    q: str | None = None,
    category_id: str | None = None,
    enabled: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[SourceResponse]:
    service = container(request).source_service
    return [source_response(item) for item in service.list(q, category_id, enabled, limit, offset)]


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


@sources_router.get("/{source_id}", response_model=dict[str, Any])
def get_source(source_id: str, request: Request) -> dict[str, Any]:
    current = container(request)
    return {
        "source": source_response(current.source_service.get(source_id)),
        "health": [
            health_response(item) for item in current.source_service.health_history(source_id)
        ],
    }


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


@collection_router.post(
    "/runs", response_model=CollectionRunResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_collection_run(payload: CollectionRunRequest, request: Request) -> CollectionRunResponse:
    current = container(request)
    source_ids = payload.source_ids
    if payload.all_enabled:
        source_ids = [source.id for source in current.source_service.list(enabled=True, limit=100)]
    run = current.collection_service.create_run(source_ids)
    task_ids = current.collection_service.task_ids_for_run(run)
    return CollectionRunResponse(
        id=run.id,
        source_ids=run.source_ids,
        status=run.status.value,
        requested_at=run.requested_at,
        task_ids=task_ids,
    )


@messages_router.get("", response_model=list[MessageResponse])
def list_messages(
    request: Request,
    q: str | None = None,
    source_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[MessageResponse]:
    values: list[MessageResponse] = []
    for message, version, translations in container(request).message_service.list(
        q, source_id, limit, offset
    ):
        values.append(
            MessageResponse(
                id=message.id,
                source_id=message.source_id,
                external_id=message.external_id,
                updated_at=message.updated_at,
                latest_version=version_response(version, translations) if version else None,
            )
        )
    return values


@messages_router.get("/{message_id}", response_model=MessageDetailResponse)
def get_message(message_id: str, request: Request) -> MessageDetailResponse:
    message, versions = container(request).message_service.detail(message_id)
    return MessageDetailResponse(
        id=message.id,
        source_id=message.source_id,
        external_id=message.external_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        versions=[version_response(version, translations) for version, translations in versions],
    )


@messages_router.post(
    "/{message_id}/translate", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED
)
def translate_message(message_id: str, request: Request) -> TaskResponse:
    return task_response(container(request).translation_service.create_task(message_id))


@tasks_router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    return task_response(container(request).task_service.get(task_id))


@providers_router.get("", response_model=list[ProviderResponse])
def list_providers(request: Request) -> list[ProviderResponse]:
    return [
        provider_response(provider, keys)
        for provider, keys in container(request).provider_service.list()
    ]


@providers_router.post("", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(payload: ProviderCreateRequest, request: Request) -> ProviderResponse:
    current = container(request)
    provider = current.provider_service.create(
        payload.name,
        payload.base_url,
        payload.model,
        payload.priority,
        payload.timeout_seconds,
        payload.session_header_name,
    )
    return provider_response(provider, [])


@providers_router.patch("/{provider_id}", response_model=ProviderResponse)
def update_provider(
    provider_id: str, payload: ProviderPatchRequest, request: Request
) -> ProviderResponse:
    current = container(request)
    provider = current.provider_service.update(provider_id, payload.model_dump(exclude_unset=True))
    return provider_response(provider, current.provider_service.keys(provider.id))


@providers_router.post(
    "/{provider_id}/keys", response_model=ProviderKeyResponse, status_code=status.HTTP_201_CREATED
)
def create_provider_key(
    provider_id: str, payload: ProviderKeyCreateRequest, request: Request
) -> ProviderKeyResponse:
    key = container(request).provider_service.add_key(
        provider_id, payload.key_ref, payload.priority
    )
    return provider_key_response(key)


@providers_router.patch("/{provider_id}/keys/{key_id}", response_model=ProviderKeyResponse)
def update_provider_key(
    provider_id: str, key_id: str, payload: ProviderKeyPatchRequest, request: Request
) -> ProviderKeyResponse:
    key = container(request).provider_service.update_key(
        provider_id, key_id, payload.model_dump(exclude_unset=True)
    )
    return provider_key_response(key)
