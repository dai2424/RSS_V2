"""全文的领域对象 → 响应模型。"""

from __future__ import annotations

from rss_v2.api.schemas import (
    ArticleCitationResponse,
    ArticleCountersResponse,
    ArticleListResponse,
    ArticleNoteResponse,
    ArticleParagraphResponse,
    ArticlePreviewResponse,
    ArticleQuoteResponse,
    ArticleSectionResponse,
    ArticleSourceResponse,
    ArticleSummaryResponse,
    ArticleVersionResponse,
)
from rss_v2.domain import ArticleCounters, ArticleVersion
from rss_v2.services.article_preview import ArticlePreview
from rss_v2.services.articles import ArticleView


def _counters(counters: ArticleCounters) -> ArticleCountersResponse:
    """计数逐字段搬运：响应里少一个字段就等于把丢弃藏起来了。"""

    return ArticleCountersResponse(
        out_of_range=counters.out_of_range,
        unsupported=counters.unsupported,
        unused_citations=counters.unused_citations,
        duplicate_markers=counters.duplicate_markers,
        invalid_markers=counters.invalid_markers,
        downgraded_speculation=counters.downgraded_speculation,
        unknown_kind=counters.unknown_kind,
        rejected_links=counters.rejected_links,
        dropped_paragraphs=counters.dropped_paragraphs,
        dropped_sections=counters.dropped_sections,
        invalid_sources=counters.invalid_sources,
        unused_sources=counters.unused_sources,
        added_sources=counters.added_sources,
        dropped_sources=counters.dropped_sources,
    )


def article_summary_response(
    version: ArticleVersion, latest_version_id: str | None
) -> ArticleSummaryResponse:
    return ArticleSummaryResponse(
        id=version.id,
        version_number=version.version_number,
        kind=version.kind,
        status=version.status,
        title=version.title,
        outline_version_id=version.outline_version_id,
        prompt_version=version.prompt_version,
        model=version.model,
        task_id=version.task_id,
        source_count=version.source_count,
        counters=_counters(version.counters),
        created_at=version.created_at,
        saved_at=version.saved_at,
        base_article_version_id=version.base_article_version_id,
        is_latest=version.id == latest_version_id,
    )


def article_version_response(
    version: ArticleVersion, latest_version_id: str | None
) -> ArticleVersionResponse:
    summary = article_summary_response(version, latest_version_id)
    return ArticleVersionResponse(
        **summary.model_dump(),
        lead=version.lead,
        sections=[
            ArticleSectionResponse(
                heading=section.heading,
                paragraphs=[
                    ArticleParagraphResponse(text=paragraph.text, kind=paragraph.kind)
                    for paragraph in section.paragraphs
                ],
            )
            for section in version.body.sections
        ],
        source_list=[
            ArticleSourceResponse(
                material_id=source.material_id,
                title=source.title,
                publisher=source.publisher,
                url=source.url,
            )
            for source in version.body.source_list
        ],
        body_markdown=version.body_markdown,
        material_snapshot_hash=version.material_snapshot_hash,
        digest_run_id=version.digest_run_id,
    )


def article_list_response(view: ArticleView) -> ArticleListResponse:
    return ArticleListResponse(
        items=[
            article_summary_response(version, view.latest_version_id) for version in view.versions
        ],
        latest_version_id=view.latest_version_id,
        saved_version_id=view.saved_version_id,
        outline_version_id=view.outline_version_id,
        has_unconfirmed_changes=view.has_unconfirmed_changes,
        has_blocking=view.has_blocking,
        blocking_count=view.blocking_count,
        task_status=view.task_status,
    )


def article_preview_response(
    preview: ArticlePreview, task_status: str | None
) -> ArticlePreviewResponse:
    version = preview.version
    return ArticlePreviewResponse(
        version=article_summary_response(version, version.id),
        title=version.title,
        lead=version.lead,
        html=preview.html,
        citations=[
            ArticleCitationResponse(
                marker=citation.marker,
                fact_id=citation.fact_id,
                fact_kind=citation.fact_kind,
                fact_status=citation.fact_status,
                statement=citation.statement,
                quotes=[
                    ArticleQuoteResponse(
                        text=quote.text,
                        locator=quote.locator,
                        material_id=quote.material_id,
                        material_title=quote.material_title,
                        material_url=quote.material_url,
                    )
                    for quote in citation.quotes
                ],
            )
            for citation in preview.citations
        ],
        pending_notes=[
            ArticleNoteResponse(kind=note.kind, text=note.text, blocking=note.blocking)
            for note in preview.pending_notes
        ],
        source_count=preview.source_count,
        single_source=preview.source_count == 1,
        has_blocking=preview.has_blocking,
        blocking_count=preview.blocking_count,
        task_status=task_status,
    )
