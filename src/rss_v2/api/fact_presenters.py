"""资料整理的领域对象 → 响应模型。"""

from __future__ import annotations

import json
from typing import cast

from rss_v2.api.schemas import (
    DigestHistoryResponse,
    DigestResponse,
    DigestRunResponse,
    EvidenceResponse,
    FactResponse,
    NoteResolutionResponse,
    NoteResponse,
)
from rss_v2.domain import DigestRun, Evidence, Fact, Note
from rss_v2.services.digest import DigestDetail


def evidence_response(evidence: Evidence) -> EvidenceResponse:
    return EvidenceResponse(
        id=evidence.id,
        material_id=evidence.material_id,
        quote=evidence.quote,
        locator=evidence.locator,
        quote_hash=evidence.quote_hash,
    )


def fact_response(fact: Fact) -> FactResponse:
    return FactResponse(
        id=fact.id,
        kind=fact.kind,
        statement=fact.statement,
        status=fact.status,
        conflict_note=fact.conflict_note,
        evidence=[evidence_response(item) for item in fact.evidence],
    )


def note_response(note: Note) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        kind=note.kind,
        text=note.text,
        fact_id=note.fact_id,
        blocking=note.blocking,
        resolved_at=note.resolved_at,
    )


def digest_run_response(run: DigestRun) -> DigestRunResponse:
    return DigestRunResponse(
        id=run.id,
        materials_hash=run.materials_hash,
        task_id=run.task_id,
        counts={
            "fact": run.counts.fact,
            "analysis": run.counts.analysis,
            "speculation": run.counts.speculation,
            "evidence": run.counts.evidence,
            "conflict": run.counts.conflict,
            "gap": run.counts.gap,
            "blocking": run.counts.blocking,
            "rejected_quotes": run.counts.rejected_quotes,
            "out_of_range": run.counts.out_of_range,
        },
        notes=list(run.notes),
        created_at=run.created_at,
    )


def digest_response(detail: DigestDetail, task_status: str | None) -> DigestResponse:
    return DigestResponse(
        run=digest_run_response(detail.run),
        facts=[fact_response(fact) for fact in detail.facts],
        notes=[note_response(note) for note in detail.notes],
        has_blocking=detail.has_blocking,
        task_status=task_status,
    )


def digest_history_response(runs: list[DigestRun]) -> DigestHistoryResponse:
    return DigestHistoryResponse(items=[digest_run_response(run) for run in runs])


def note_resolution_response(note: Note) -> NoteResolutionResponse:
    return NoteResolutionResponse(note=note_response(note))


def digest_summary_raw(raw_json: str) -> dict[str, object]:
    """项目上的整理摘要原文（前端展示"上一轮跑到哪、有没有未处理的阻塞"）。"""

    try:
        parsed: object = json.loads(raw_json or "{}")
    except ValueError:
        return {}
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
