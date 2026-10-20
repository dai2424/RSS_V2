"""提纲版本的 SQLite 仓储。

版本只追加：模型生成与人工保存都写新行，历史行不被改写。确认只改状态列，并在同一
事务里把旧的已确认版本转为 superseded——"至多一个已确认版本"是数据约束，不能靠
调用顺序保证。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.lease import ensure_lease
from rss_v2.domain import OutlineCounters, OutlineSection, OutlineVersion, TaskLease

logger = logging.getLogger(__name__)

#: 计数字段：写读两侧共用一份名单，新增计数时不会只改一半。
_COUNTER_FIELDS = (
    "out_of_range",
    "unsupported",
    "dropped_sections",
    "dropped_points",
    "without_facts",
)


def _sections(raw: str) -> tuple[OutlineSection, ...]:
    """解析分节 JSON；结构损坏时记录原因并返回空，不静默当成"没有分节"。"""

    try:
        parsed: object = json.loads(raw or "[]")
    except ValueError:
        logger.warning("outline_sections_unparsable")
        return ()
    if not isinstance(parsed, list):
        logger.warning("outline_sections_not_a_list type=%s", type(parsed).__name__)
        return ()
    sections: list[OutlineSection] = []
    for item in cast(list[object], parsed):
        if not isinstance(item, dict):
            logger.warning("outline_section_not_an_object")
            continue
        row = cast(dict[str, object], item)
        sections.append(
            OutlineSection(
                heading=str(row.get("heading") or ""),
                talking_points=_texts(row.get("talking_points")),
                fact_ids=_texts(row.get("fact_ids")),
                evidence_ids=_texts(row.get("evidence_ids")),
                impact_notes=_texts(row.get("impact_notes")),
            )
        )
    return tuple(sections)


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in cast(list[object], value))


def dump_counters(counters: OutlineCounters) -> str:
    """计数序列化为 JSON。"""

    return json.dumps({name: getattr(counters, name) for name in _COUNTER_FIELDS})


def _counters(raw: str) -> OutlineCounters:
    """解析计数 JSON；结构损坏时记录原因并按零值处理（计数只用于提示，不影响提纲本身）。"""

    try:
        parsed: object = json.loads(raw or "{}")
    except ValueError:
        logger.warning("outline_counters_unparsable")
        return OutlineCounters()
    if not isinstance(parsed, dict):
        logger.warning("outline_counters_not_an_object type=%s", type(parsed).__name__)
        return OutlineCounters()
    row = cast(dict[str, object], parsed)
    return OutlineCounters(
        out_of_range=_int(row.get("out_of_range")),
        unsupported=_int(row.get("unsupported")),
        dropped_sections=_int(row.get("dropped_sections")),
        dropped_points=_int(row.get("dropped_points")),
        without_facts=_int(row.get("without_facts")),
    )


def _int(value: object) -> int:
    """JSON 里的计数取值：只认数字，其它形态按 0 处理（计数只用于提示，不影响提纲）。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def dump_sections(sections: tuple[OutlineSection, ...]) -> str:
    """分节序列化为 JSON；写读两侧都在这个模块里，字段名只有一份。"""

    return json.dumps(
        [
            {
                "heading": section.heading,
                "talking_points": list(section.talking_points),
                "fact_ids": list(section.fact_ids),
                "evidence_ids": list(section.evidence_ids),
                "impact_notes": list(section.impact_notes),
            }
            for section in sections
        ],
        ensure_ascii=False,
    )


def _version(row: sqlite3.Row) -> OutlineVersion:
    return OutlineVersion(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        version_number=int(row["version_number"]),
        status=str(row["status"]),
        source=str(row["source"]),
        title=str(row["title"]),
        angle=str(row["angle"]),
        sections=_sections(str(row["sections_json"] or "[]")),
        material_snapshot_hash=str(row["material_snapshot_hash"]),
        counters=_counters(str(row["counters_json"] or "{}")),
        digest_run_id=str(row["digest_run_id"]) if row["digest_run_id"] is not None else None,
        prompt_version=str(row["prompt_version"]) if row["prompt_version"] is not None else None,
        model=str(row["model"]) if row["model"] is not None else None,
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        based_on_version=(
            str(row["based_on_version"]) if row["based_on_version"] is not None else None
        ),
        created_at=int(row["created_at"]),
        confirmed_at=int(row["confirmed_at"]) if row["confirmed_at"] is not None else None,
    )


class SQLiteOutlineRepository:
    """提纲版本的持久化。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def add(self, version: OutlineVersion, lease: TaskLease | None = None) -> None:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            connection.execute(
                "INSERT INTO outline_versions(id,project_id,version_number,status,source,title,"
                " angle,sections_json,material_snapshot_hash,counters_json,digest_run_id,"
                " prompt_version,model,task_id,based_on_version,created_at,confirmed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version.id,
                    version.project_id,
                    version.version_number,
                    version.status,
                    version.source,
                    version.title,
                    version.angle,
                    dump_sections(version.sections),
                    version.material_snapshot_hash,
                    dump_counters(version.counters),
                    version.digest_run_id,
                    version.prompt_version,
                    version.model,
                    version.task_id,
                    version.based_on_version,
                    version.created_at,
                    version.confirmed_at,
                ),
            )

    def get(self, version_id: str) -> OutlineVersion | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM outline_versions WHERE id=?", (version_id,)
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def versions(self, project_id: str, limit: int = 20) -> list[OutlineVersion]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM outline_versions WHERE project_id=?"
                " ORDER BY version_number DESC, rowid DESC LIMIT ?",
                (project_id, max(1, limit)),
            ).fetchall()
            return [_version(row) for row in rows]
        finally:
            connection.close()

    def latest(self, project_id: str) -> OutlineVersion | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM outline_versions WHERE project_id=?"
                " ORDER BY version_number DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def confirmed(self, project_id: str) -> OutlineVersion | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM outline_versions WHERE project_id=? AND status='confirmed'"
                " ORDER BY version_number DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def latest_number(self, project_id: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(version_number),0) AS n FROM outline_versions WHERE project_id=?",
                (project_id,),
            ).fetchone()
            return int(row["n"])
        finally:
            connection.close()

    def count(self, project_id: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM outline_versions WHERE project_id=?", (project_id,)
            ).fetchone()
            return int(row["n"])
        finally:
            connection.close()

    def confirm(
        self, project_id: str, version_id: str, timestamp: int, lease: TaskLease | None = None
    ) -> int:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            cursor = connection.execute(
                "UPDATE outline_versions SET status='confirmed', confirmed_at=?"
                " WHERE id=? AND project_id=? AND status<>'superseded'"
                " AND version_number=(SELECT MAX(version_number) FROM outline_versions"
                " WHERE project_id=?)",
                (timestamp, version_id, project_id, project_id),
            )
            if cursor.rowcount <= 0:
                return 0
            connection.execute(
                "UPDATE outline_versions SET status='superseded'"
                " WHERE project_id=? AND status='confirmed' AND id<>?",
                (project_id, version_id),
            )
            return cursor.rowcount

    def delete(self, version_id: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM outline_versions WHERE id=?", (version_id,))
            return cursor.rowcount if cursor.rowcount > 0 else 0
