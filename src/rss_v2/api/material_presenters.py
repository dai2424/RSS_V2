"""资料与资料摘要的领域对象 → 响应模型。"""

from __future__ import annotations

import json
from typing import cast

from rss_v2.api.schemas import (
    MaterialDetailResponse,
    MaterialQueryResponse,
    MaterialResponse,
    MaterialsSummaryResponse,
)
from rss_v2.domain import Material


def material_response(material: Material) -> MaterialResponse:
    return MaterialResponse(
        id=material.id,
        project_id=material.project_id,
        kind=material.kind,
        status=material.status,
        url=material.url,
        final_url=material.final_url,
        title=material.title,
        publisher=material.publisher,
        published_at=material.published_at,
        fetched_at=material.fetched_at,
        content_hash=material.content_hash,
        byte_size=material.byte_size,
        truncated=material.truncated,
        message_version_id=material.message_version_id,
        error_code=material.error_code,
        error_message=material.error_message,
        created_at=material.created_at,
    )


def material_detail_response(material: Material) -> MaterialDetailResponse:
    return MaterialDetailResponse(
        **material_response(material).model_dump(), content_text=material.content_text
    )


def materials_summary_response(raw_json: str) -> MaterialsSummaryResponse:
    """摘要 JSON → 响应；内容坏了就返回空摘要（界面显示"还没有资料准备记录"）。"""

    try:
        parsed: object = json.loads(raw_json or "{}")
    except ValueError:
        parsed = {}
    record = cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
    queries: list[MaterialQueryResponse] = []
    raw_queries = record.get("queries")
    if isinstance(raw_queries, list):
        for item in cast(list[object], raw_queries):
            if not isinstance(item, dict):
                continue
            row = cast(dict[str, object], item)
            engines = row.get("unresponsive_engines")
            queries.append(
                MaterialQueryResponse(
                    query=str(row.get("query") or ""),
                    purpose=str(row.get("purpose") or ""),
                    hits=_as_int(row.get("hits")),
                    unresponsive_engines=[str(engine) for engine in cast(list[object], engines)]
                    if isinstance(engines, list)
                    else [],
                    error_code=str(row.get("error_code") or ""),
                )
            )
    notes = record.get("notes")
    return MaterialsSummaryResponse(
        queries=queries,
        read=_as_int(record.get("read")),
        summary_only=_as_int(record.get("summary_only")),
        unavailable=_as_int(record.get("unavailable")),
        failed=_as_int(record.get("failed")),
        duplicates=_as_int(record.get("duplicates")),
        skipped_by_budget=_as_int(record.get("skipped_by_budget")),
        notes=[str(note) for note in cast(list[object], notes)] if isinstance(notes, list) else [],
        task_id=str(record["task_id"]) if record.get("task_id") else None,
        finished_at=_as_int(record.get("finished_at")),
    )


def _as_int(value: object) -> int:
    """摘要里的计数容错读取：坏值按 0 处理，不让一条脏数据打断列表。"""

    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0
