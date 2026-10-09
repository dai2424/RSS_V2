"""关键词、合并与相关消息的响应转换。

与 presenters.py 分开只为控制单文件规模：这里全是关键词域的搬运，没有业务判断。
"""

from __future__ import annotations

from rss_v2.api.schemas import (
    KeywordBucketResponse,
    KeywordEntryResponse,
    KeywordKindStatResponse,
    KeywordMergePreviewResponse,
    KeywordMergeRecordResponse,
    KeywordMergeResponse,
    KeywordOverviewResponse,
    KeywordResponse,
    KeywordSourceStatResponse,
    KeywordTrendPointResponse,
    RelatedMessageResponse,
)
from rss_v2.domain import (
    KeywordEntry,
    KeywordOverview,
    MergePreview,
    MergeRecord,
    RelatedMessage,
)


def keyword_entry_response(item: KeywordEntry) -> KeywordEntryResponse:
    return KeywordEntryResponse(
        key=item.key,
        text=item.text,
        kind=item.kind.value,
        mentions=item.mentions,
        sources=item.sources,
        first_seen_at=item.first_seen_at,
        last_seen_at=item.last_seen_at,
        aliases=list(item.aliases),
    )


def keyword_overview_response(item: KeywordOverview) -> KeywordOverviewResponse:
    return KeywordOverviewResponse(
        messages=item.messages,
        enriched=item.enriched,
        pending=item.pending,
        terms=item.terms,
        mentions=item.mentions,
        singletons=item.singletons,
        average_per_message=item.average_per_message,
        aliases=item.aliases,
        merges=item.merges,
        kinds=[
            KeywordKindStatResponse(kind=stat.kind.value, terms=stat.terms, mentions=stat.mentions)
            for stat in item.kinds
        ],
        long_tail=[
            KeywordBucketResponse(label=bucket.label, terms=bucket.terms)
            for bucket in item.long_tail
        ],
        trend=[
            KeywordTrendPointResponse(
                day=point.day, mentions=point.mentions, new_terms=point.new_terms
            )
            for point in item.trend
        ],
        sources=[
            KeywordSourceStatResponse(
                source_id=stat.source_id,
                source_name=stat.source_name,
                terms=stat.terms,
                mentions=stat.mentions,
                top=[
                    KeywordResponse(text=keyword.text, kind=keyword.kind.value)
                    for keyword in stat.top
                ],
            )
            for stat in item.sources
        ],
        tasks_queued=item.tasks_queued,
        tasks_running=item.tasks_running,
        tasks_succeeded=item.tasks_succeeded,
        tasks_failed=item.tasks_failed,
    )


def _merge_preview_response(item: MergePreview) -> KeywordMergePreviewResponse:
    return KeywordMergePreviewResponse(
        target=item.target,
        sources=list(item.sources),
        forms=[
            KeywordResponse(text=keyword.text, kind=keyword.kind.value) for keyword in item.forms
        ],
        mentions=item.mentions,
        messages=item.messages,
    )


def merge_record_response(item: MergeRecord) -> KeywordMergeRecordResponse:
    return KeywordMergeRecordResponse(
        id=item.id,
        target_key=item.target_key,
        target_raw=item.target_raw,
        members=[
            KeywordResponse(text=keyword.text, kind=keyword.kind.value) for keyword in item.members
        ],
        messages=item.messages,
        created_at=item.created_at,
        undone_at=item.undone_at,
    )


def merge_response(record: MergeRecord, preview: MergePreview) -> KeywordMergeResponse:
    return KeywordMergeResponse(
        record=merge_record_response(record), preview=_merge_preview_response(preview)
    )


def merge_preview_response(item: MergePreview) -> KeywordMergePreviewResponse:
    return _merge_preview_response(item)


def related_message_response(item: RelatedMessage) -> RelatedMessageResponse:
    return RelatedMessageResponse(
        message_id=item.message_id,
        source_id=item.source_id,
        version_id=item.version_id,
        title=item.title,
        published_at=item.published_at,
        collected_at=item.collected_at,
        shared=[
            KeywordResponse(text=keyword.text, kind=keyword.kind.value) for keyword in item.shared
        ],
    )
