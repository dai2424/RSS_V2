"""API 容器访问与领域响应转换。"""

from __future__ import annotations

from fastapi import Request

from rss_v2.api.schemas import (
    CategoryResponse,
    EnrichmentResponse,
    HealthResponse,
    MessageVersionResponse,
    PromptCompileIssue,
    PromptCompileResponse,
    PromptResponse,
    PromptTestResponse,
    PromptUsageResponse,
    ProviderKeyResponse,
    ProviderModelResponse,
    ProviderResponse,
    SourceResponse,
    TaskResponse,
    TaskSettingResponse,
    TranslationResponse,
)
from rss_v2.bootstrap import Container
from rss_v2.domain import (
    Category,
    Enrichment,
    HealthCheck,
    MessageVersion,
    Prompt,
    PromptTest,
    PromptUsage,
    Provider,
    ProviderKey,
    ProviderModel,
    Source,
    Task,
    TaskSettingView,
    TaskView,
    Translation,
)
from rss_v2.domain.prompts import CompiledPrompt, spec_for
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


def _snapshot_text(value: object) -> str | None:
    """任务快照里的文本字段；缺失或空串统一算未知。"""

    text = str(value or "").strip()
    return text or None


def task_response(task: Task) -> TaskResponse:
    """任务响应；模型与提示词版本从任务快照读取，都是非敏感参数。"""

    return TaskResponse(
        id=task.id,
        task_type=task.task_type,
        idempotency_key=task.idempotency_key,
        status=task.status,
        attempts=task.attempts,
        lease_until=task.lease_until,
        input_version_id=task.input_version_id,
        output_version_id=task.output_version_id,
        error_code=task.error_code,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        model=_snapshot_text(task.payload.get("model")),
        prompt_version=_snapshot_text(task.payload.get("prompt_version")),
    )


def task_view_response(view: TaskView) -> TaskResponse:
    """任务列表项：在任务响应上补目标说明。"""

    return task_response(view.task).model_copy(
        update={
            "target_kind": view.target_kind,
            "target_id": view.target_id or None,
            "target_label": view.target_label,
        }
    )


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
        protocol=provider.protocol,
        enabled=provider.enabled,
        timeout_seconds=provider.timeout_seconds,
        session_header_name=provider.session_header_name,
        keys=[provider_key_response(key) for key in keys],
        models=[provider_model_response(model) for model in models],
        extra_headers=provider.extra_headers,
    )


def prompt_response(prompt: Prompt) -> PromptResponse:
    return PromptResponse(
        id=prompt.id,
        task_kind=prompt.task_kind,
        prompt_key=prompt.prompt_key,
        version=prompt.version,
        version_string=prompt.version_string,
        name=prompt.name,
        status=prompt.status,
        system_template=prompt.system_template,
        user_template=prompt.user_template,
        note=prompt.note,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


def compile_response(compiled: CompiledPrompt, task_kind: str) -> PromptCompileResponse:
    """编译结果加上该任务类型的可用占位符，界面据此给出提示。"""

    spec = spec_for(task_kind)
    return PromptCompileResponse(
        ok=compiled.ok,
        errors=[
            PromptCompileIssue(field=item.field, message=item.message) for item in compiled.errors
        ],
        warnings=[
            PromptCompileIssue(field=item.field, message=item.message) for item in compiled.warnings
        ],
        system=compiled.system,
        user=compiled.user,
        variables=list(spec.variables),
    )


def prompt_usage_response(usage: PromptUsage) -> PromptUsageResponse:
    """使用情况；判定口径在仓储层，这里只做字段搬运。"""

    return PromptUsageResponse(
        used=usage.used,
        calls=usage.calls,
        results=usage.results,
        tasks=usage.tasks,
        bindings=usage.bindings,
    )


def prompt_test_response(result: PromptTest) -> PromptTestResponse:
    return PromptTestResponse(
        ok=result.ok,
        model=result.model,
        key_masked=result.key_masked,
        latency_ms=result.latency_ms,
        total_tokens=result.total_tokens,
        system=result.system,
        user=result.user,
        output=result.output,
        error_code=result.error_code,
        error_message=result.error_message,
    )


def task_setting_response(view: TaskSettingView) -> TaskSettingResponse:
    return TaskSettingResponse(
        task_kind=view.task_kind,
        label=view.label,
        enabled=view.enabled,
        prompt_id=view.prompt_id,
        effective_enabled=view.effective_enabled,
        effective_prompt_id=view.effective_prompt_id,
        effective_prompt_version=view.effective_prompt_version,
        scope=view.scope,
    )
