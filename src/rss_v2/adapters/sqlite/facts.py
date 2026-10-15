"""资料整理的 SQLite 仓储：资料包版本、支持证据、事实与待办。

写入按版本（run）追加：重跑新增版本而不改写历史；待办可人工标记已处理，
重跑时保留已处理的记录（同一项目里已处理的缺口不会因为重跑又变回待处理）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.lease import ensure_lease
from rss_v2.domain import (
    DigestCounts,
    DigestRun,
    Evidence,
    Fact,
    Note,
    TaskLease,
)

_COUNT_COLUMNS = (
    "fact_count",
    "analysis_count",
    "speculation_count",
    "evidence_count",
    "conflict_count",
    "gap_count",
    "blocking_count",
    "rejected_quotes",
    "out_of_range",
)


def _run(row: sqlite3.Row) -> DigestRun:
    raw = str(row["notes_json"] or "[]")
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        parsed = []
    items = cast(list[object], parsed) if isinstance(parsed, list) else []
    notes = tuple(str(item) for item in items)
    return DigestRun(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        materials_hash=str(row["materials_hash"]),
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        counts=DigestCounts(
            fact=int(row["fact_count"]),
            analysis=int(row["analysis_count"]),
            speculation=int(row["speculation_count"]),
            evidence=int(row["evidence_count"]),
            conflict=int(row["conflict_count"]),
            gap=int(row["gap_count"]),
            blocking=int(row["blocking_count"]),
            rejected_quotes=int(row["rejected_quotes"]),
            out_of_range=int(row["out_of_range"]),
        ),
        notes=notes,
        created_at=int(row["created_at"]),
    )


def _evidence(row: sqlite3.Row) -> Evidence:
    return Evidence(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        material_id=str(row["material_id"]),
        quote=str(row["quote"]),
        locator=int(row["locator"]),
        quote_hash=str(row["quote_hash"]),
        created_at=int(row["created_at"]),
    )


def _note(row: sqlite3.Row) -> Note:
    return Note(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        kind=str(row["kind"]),
        text=str(row["text"]),
        fact_id=str(row["fact_id"]) if row["fact_id"] is not None else None,
        blocking=bool(row["blocking"]),
        resolved_at=int(row["resolved_at"]) if row["resolved_at"] is not None else None,
        created_at=int(row["created_at"]),
    )


class SQLiteFactRepository:
    """事实、证据、待办与版本记录的持久化。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create_run(self, run_id: str, project_id: str, materials_hash: str, timestamp: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO digest_runs(id,project_id,materials_hash,created_at) VALUES(?,?,?,?)",
                (run_id, project_id, materials_hash, timestamp),
            )

    def finish_run(
        self,
        run_id: str,
        task_id: str,
        counts: DigestCounts,
        notes: Sequence[str],
        lease: TaskLease | None = None,
    ) -> int:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            cursor = connection.execute(
                "UPDATE digest_runs SET task_id=?,"
                " fact_count=?, analysis_count=?, speculation_count=?, evidence_count=?,"
                " conflict_count=?, gap_count=?, blocking_count=?, rejected_quotes=?,"
                " out_of_range=?, notes_json=? WHERE id=?",
                (
                    task_id,
                    counts.fact,
                    counts.analysis,
                    counts.speculation,
                    counts.evidence,
                    counts.conflict,
                    counts.gap,
                    counts.blocking,
                    counts.rejected_quotes,
                    counts.out_of_range,
                    json.dumps(list(notes), ensure_ascii=False),
                    run_id,
                ),
            )
            return cursor.rowcount if cursor.rowcount > 0 else 0

    def add_evidence(self, evidence: Evidence, lease: TaskLease | None = None) -> None:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            connection.execute(
                "INSERT INTO material_evidence(id,run_id,project_id,material_id,quote,locator,"
                " quote_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    evidence.id,
                    evidence.run_id,
                    evidence.project_id,
                    evidence.material_id,
                    evidence.quote,
                    evidence.locator,
                    evidence.quote_hash,
                    evidence.created_at,
                ),
            )

    def add_fact(
        self, fact: Fact, evidence_ids: Sequence[str], lease: TaskLease | None = None
    ) -> None:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            connection.execute(
                "INSERT INTO project_facts(id,run_id,project_id,kind,statement,status,"
                " conflict_note,conflict_refs_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    fact.id,
                    fact.run_id,
                    fact.project_id,
                    fact.kind,
                    fact.statement,
                    fact.status,
                    fact.conflict_note,
                    json.dumps(list(fact.conflict_refs), ensure_ascii=False),
                    fact.created_at,
                ),
            )
            connection.executemany(
                "INSERT INTO fact_evidence(fact_id,evidence_id) VALUES(?,?)"
                " ON CONFLICT(fact_id,evidence_id) DO NOTHING",
                [(fact.id, evidence_id) for evidence_id in evidence_ids],
            )

    def add_note(self, note: Note, lease: TaskLease | None = None) -> None:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            connection.execute(
                "INSERT INTO project_notes(id,run_id,project_id,kind,text,fact_id,blocking,"
                " resolved_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    note.id,
                    note.run_id,
                    note.project_id,
                    note.kind,
                    note.text,
                    note.fact_id,
                    1 if note.blocking else 0,
                    note.resolved_at,
                    note.created_at,
                ),
            )

    def latest_run(self, project_id: str) -> str | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT id FROM digest_runs WHERE project_id=?"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return str(row["id"]) if row else None
        finally:
            connection.close()

    def runs(self, project_id: str, limit: int = 10) -> list[str]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT id FROM digest_runs WHERE project_id=?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, max(1, limit)),
            ).fetchall()
            return [str(row["id"]) for row in rows]
        finally:
            connection.close()

    def get_run(self, run_id: str) -> DigestRun | None:
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT * FROM digest_runs WHERE id=?", (run_id,)).fetchone()
            return _run(row) if row else None
        finally:
            connection.close()

    def facts(self, run_id: str) -> list[Fact]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM project_facts WHERE run_id=? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
            facts: list[Fact] = []
            for row in rows:
                evidence = connection.execute(
                    "SELECT e.* FROM material_evidence e"
                    " JOIN fact_evidence fe ON fe.evidence_id = e.id"
                    " WHERE fe.fact_id=? ORDER BY e.created_at, e.rowid",
                    (str(row["id"]),),
                ).fetchall()
                refs_raw = str(row["conflict_refs_json"] or "[]")
                try:
                    refs: object = json.loads(refs_raw)
                except ValueError:
                    refs = []
                refs_list = cast(list[object], refs) if isinstance(refs, list) else []
                facts.append(
                    Fact(
                        id=str(row["id"]),
                        run_id=str(row["run_id"]),
                        project_id=str(row["project_id"]),
                        kind=str(row["kind"]),
                        statement=str(row["statement"]),
                        status=str(row["status"]),
                        conflict_note=str(row["conflict_note"]),
                        conflict_refs=tuple(str(item) for item in refs_list),
                        evidence=tuple(_evidence(item) for item in evidence),
                        created_at=int(row["created_at"]),
                    )
                )
            return facts
        finally:
            connection.close()

    def notes(self, run_id: str) -> list[Note]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM project_notes WHERE run_id=? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
            return [_note(row) for row in rows]
        finally:
            connection.close()

    def get_note(self, note_id: str) -> Note | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM project_notes WHERE id=?", (note_id,)
            ).fetchone()
            return _note(row) if row else None
        finally:
            connection.close()

    def resolve_note(self, note_id: str, timestamp: int) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE project_notes SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
                (timestamp, note_id),
            )
            return cursor.rowcount if cursor.rowcount > 0 else 0

    def unresolved_blocking(self, project_id: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM project_notes"
                " WHERE project_id=? AND blocking=1 AND resolved_at IS NULL",
                (project_id,),
            ).fetchone()
            return int(row["n"])
        finally:
            connection.close()

    def delete_run(self, run_id: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM digest_runs WHERE id=?", (run_id,))
            return cursor.rowcount if cursor.rowcount > 0 else 0
