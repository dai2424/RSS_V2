"""提纲的领域对象 → 响应模型。"""

from __future__ import annotations

from rss_v2.api.schemas import (
    OutlineCountersResponse,
    OutlineListResponse,
    OutlineResponse,
    OutlineSaveRequest,
    OutlineSectionResponse,
    OutlineVersionResponse,
)
from rss_v2.domain import OutlineDraft, OutlineSection, OutlineSectionSchema, OutlineVersion
from rss_v2.services.outlines import OutlineView


def outline_section_response(section: OutlineSection) -> OutlineSectionResponse:
    return OutlineSectionResponse(
        heading=section.heading,
        talking_points=list(section.talking_points),
        fact_ids=list(section.fact_ids),
        evidence_ids=list(section.evidence_ids),
        impact_notes=list(section.impact_notes),
    )


def outline_version_response(
    version: OutlineVersion, latest_version_id: str | None
) -> OutlineVersionResponse:
    return OutlineVersionResponse(
        id=version.id,
        version_number=version.version_number,
        status=version.status,
        source=version.source,
        title=version.title,
        angle=version.angle,
        sections=[outline_section_response(section) for section in version.sections],
        counters=OutlineCountersResponse(
            out_of_range=version.counters.out_of_range,
            unsupported=version.counters.unsupported,
            dropped_sections=version.counters.dropped_sections,
            dropped_points=version.counters.dropped_points,
            without_facts=version.counters.without_facts,
        ),
        material_snapshot_hash=version.material_snapshot_hash,
        digest_run_id=version.digest_run_id,
        prompt_version=version.prompt_version,
        model=version.model,
        task_id=version.task_id,
        based_on_version=version.based_on_version,
        created_at=version.created_at,
        confirmed_at=version.confirmed_at,
        is_latest=version.id == latest_version_id,
    )


def outline_list_response(view: OutlineView) -> OutlineListResponse:
    return OutlineListResponse(
        items=[
            outline_version_response(version, view.latest_version_id) for version in view.versions
        ],
        latest_version_id=view.latest_version_id,
        confirmed_version_id=view.confirmed_version_id,
        has_unconfirmed_changes=view.has_unconfirmed_changes,
        has_blocking=view.has_blocking,
        blocking_count=view.blocking_count,
        task_status=view.task_status,
    )


def outline_response(version: OutlineVersion, view: OutlineView) -> OutlineResponse:
    return OutlineResponse(
        version=outline_version_response(version, view.latest_version_id),
        latest_version_id=view.latest_version_id,
        confirmed_version_id=view.confirmed_version_id,
        has_unconfirmed_changes=view.has_unconfirmed_changes,
        has_blocking=view.has_blocking,
        blocking_count=view.blocking_count,
        task_status=view.task_status,
    )


def outline_draft(request: OutlineSaveRequest) -> OutlineDraft:
    """请求体 → 生成草稿：人工保存与模型输出共用同一个 schema 与校验规则。

    请求模型与 `OutlineSectionSchema` 字段一一对应，这里逐字段搬运而不是直接复用
    请求模型，是为了让"接口形状"与"校验形状"能各自演进（接口加展示字段不会悄悄
    改变校验口径）。
    """

    return OutlineDraft(
        title=request.title,
        angle=request.angle,
        sections=[
            OutlineSectionSchema(
                heading=section.heading,
                talking_points=list(section.talking_points),
                fact_ids=list(section.fact_ids),
                evidence_ids=list(section.evidence_ids),
                impact_notes=list(section.impact_notes),
            )
            for section in request.sections
        ],
    )
