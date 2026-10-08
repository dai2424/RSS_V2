"""API 容器访问与领域响应转换。"""

from __future__ import annotations

from fastapi import Request

from rss_v2.api.schemas import (
    CategoryResponse,
    EnrichmentResponse,
    HealthResponse,
    MessageVersionResponse,
    ProviderKeyResponse,
    ProviderModelResponse,
    ProviderResponse,
    SourceResponse,
    TaskResponse,
    TranslationResponse,
)
from rss_v2.bootstrap import Container
from rss_v2.domain import (
    Category,
    Enrichment,
    HealthCheck,
    MessageVersion,
    Provider,
    ProviderKey,
    ProviderModel,
    Source,
    Task,
    Translation,
)
from rss_v2.domain.values import mask_secret


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


def enrichment_response(enrichment: Enrichment) -> EnrichmentResponse:
    return EnrichmentResponse(
        id=enrichment.id,
        message_version_id=enrichment.message_version_id,
        status=enrichment.status,
        title=enrichment.title,
        summary=enrichment.summary,
        keywords=list(enrichment.keywords),
        provider_id=enrichment.provider_id,
        key_masked=enrichment.key_masked,
        model=enrichment.model,
        prompt_version=enrichment.prompt_version,
        task_id=enrichment.task_id,
        error_code=enrichment.error_code,
        error_message=enrichment.error_message,
        created_at=enrichment.created_at,
        updated_at=enrichment.updated_at,
    )


def version_response(
    version: MessageVersion,
    translations: list[Translation],
    enrichments: list[Enrichment] | None = None,
    translation_task: Task | None = None,
    enrichment_task: Task | None = None,
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
        enrichments=[enrichment_response(item) for item in (enrichments or [])],
        translation_task=task_response(translation_task) if translation_task else None,
        enrichment_task=task_response(enrichment_task) if enrichment_task else None,
    )


def task_response(task: Task) -> TaskResponse:
    return TaskResponse.model_validate(task, from_attributes=True)


def provider_key_response(key: ProviderKey) -> ProviderKeyResponse:
    return ProviderKeyResponse(
        id=key.id,
        provider_id=key.provider_id,
        masked=mask_secret(key.secret),
        priority=key.priority,
        enabled=key.enabled,
        cooldown_until=key.cooldown_until,
        last_status=key.last_status,
    )


def provider_model_response(model: ProviderModel) -> ProviderModelResponse:
    return ProviderModelResponse.model_validate(model, from_attributes=True)


def provider_response(
    provider: Provider, models: list[ProviderModel], keys: list[ProviderKey]
) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        enabled=provider.enabled,
        timeout_seconds=provider.timeout_seconds,
        session_header_name=provider.session_header_name,
        keys=[provider_key_response(key) for key in keys],
        models=[provider_model_response(model) for model in models],
        extra_headers=provider.extra_headers,
    )
