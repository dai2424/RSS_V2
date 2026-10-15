"""选题发现的 SQLite 仓储：发现输入读取、批次/候选、写作项目。

三件事放在一个模块里，因为它们服务同一个用例，且共用发现输入的读取口径：

- `SQLiteDiscoveryScopeReader`：按时间窗口与分类/来源读取消息（每个消息取最新版本），
  并带上当前生效加工结果的关键词。**不按关键词过滤**：没有加工结果的消息同样返回，
  短快讯才有机会进入选题。
- `SQLiteDiscoveryRepository`：批次与候选。候选的成员存在 `lead_messages`（消息版本），
  候选行只存事件级信息；重试时按批次替换候选，避免同一次发现叠出两份结果。
- `SQLiteWritingProjectRepository`：写作项目。同一候选只对应一个项目，重复选择幂等。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_sql import CURRENT_ENRICHMENT, LATEST_VERSION, RESOLVED_KEY
from rss_v2.adapters.sqlite.lease import ensure_lease
from rss_v2.domain import (
    DiscoveryBatch,
    DiscoveryCandidate,
    DiscoveryInputVersion,
    RecommendationReason,
    TaskLease,
    WritingProject,
)

#: 发现输入：窗口内每个消息的最新版本 + 来源/分类；时间口径与列表一致（发布优先，采集回落）。
_SCOPE = f"""
SELECT m.id AS message_id, v.id AS version_id, v.version_number, v.title, v.summary, v.content,
       v.url, v.published_at, v.collected_at,
       s.id AS source_id, s.name AS source_name, c.id AS category_id, c.name AS category_name
FROM messages m
JOIN message_versions v ON v.message_id = m.id AND v.id = {LATEST_VERSION}
JOIN rss_sources s ON s.id = m.source_id
JOIN categories c ON c.id = s.category_id
WHERE COALESCE(v.published_at, v.collected_at) >= ?
  AND COALESCE(v.published_at, v.collected_at) < ?
"""

#: 关键词按重要度（模型给出的 ordinal）排序，作为模型判断的附加上下文。
_KEYWORDS = f"""
SELECT k.message_version_id AS version_id, {RESOLVED_KEY} AS keyword_text
FROM enrichment_keywords k
JOIN message_versions v ON v.id = k.message_version_id
LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
  AND k.message_version_id IN ({{placeholders}})
ORDER BY k.message_version_id, k.ordinal
"""


def _batch(row: sqlite3.Row) -> DiscoveryBatch:
    return DiscoveryBatch(
        id=str(row["id"]),
        window_start=int(row["window_start"]),
        window_end=int(row["window_end"]),
        category_id=str(row["category_id"]) if row["category_id"] is not None else None,
        source_id=str(row["source_id"]) if row["source_id"] is not None else None,
        input_fingerprint=str(row["input_fingerprint"]),
        idempotency_key=str(row["idempotency_key"]),
        status=str(row["status"]),
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        source_label=str(row["source_label"]),
        message_count=int(row["message_count"]),
        candidate_count=int(row["candidate_count"]),
        duplicate_members=int(row["duplicate_members"]),
        out_of_range=int(row["out_of_range"]),
        dropped_members=int(row["dropped_members"]),
        dropped_candidates=int(row["dropped_candidates"]),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        error_message=str(row["error_message"]) if row["error_message"] is not None else None,
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _candidate(row: sqlite3.Row) -> DiscoveryCandidate:
    raw = str(row["reasons_json"] or "[]")
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        # 库里的数据坏了不该让详情整页失败：理由为空比抛异常更有用，页面还有标题与成员。
        parsed = []
    reasons: list[RecommendationReason] = []
    if isinstance(parsed, list):
        for item in cast(list[object], parsed):
            if isinstance(item, dict):
                # json.loads 的键值都是 JSON 基本类型；这里只取两个字符串字段。
                record = cast(dict[str, object], item)
                reasons.append(
                    RecommendationReason(
                        kind=str(record.get("kind") or ""), text=str(record.get("text") or "")
                    )
                )
    return DiscoveryCandidate(
        id=str(row["id"]),
        batch_id=str(row["batch_id"]),
        lead_id=str(row["lead_id"]),
        rank=int(row["rank"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        event_time=int(row["event_time"]) if row["event_time"] is not None else None,
        score=int(row["score"]),
        reasons=tuple(reasons),
        member_count=int(row["member_count"]),
        dropped_members=int(row["dropped_members"]),
        created_at=int(row["created_at"]),
    )


def _project(row: sqlite3.Row) -> WritingProject:
    return WritingProject(
        id=str(row["id"]),
        lead_id=str(row["lead_id"]),
        candidate_id=str(row["candidate_id"]) if row["candidate_id"] is not None else None,
        title=str(row["title"]),
        status=str(row["status"]),
        materials_summary_json=str(row["materials_summary_json"] or "{}"),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


class SQLiteDiscoveryScopeReader:
    """发现输入读取端口：窗口内的消息版本（最新版本）与可用的关键词。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def scope_versions(
        self,
        since: int,
        until: int,
        source_id: str | None,
        category_id: str | None,
        limit: int,
        offset: int,
    ) -> list[DiscoveryInputVersion]:
        where, args = self._scope_filters(source_id, category_id)
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"{_SCOPE}{where} ORDER BY COALESCE(v.published_at, v.collected_at), m.id LIMIT ? OFFSET ?",
                [since, until, *args, max(1, limit), max(0, offset)],
            ).fetchall()
            items = [self._input(row) for row in rows]
            keywords = self._keywords(connection, [item.version_id for item in items])
            return [
                DiscoveryInputVersion(
                    message_id=item.message_id,
                    version_id=item.version_id,
                    version_number=item.version_number,
                    title=item.title,
                    summary=item.summary,
                    content=item.content,
                    url=item.url,
                    published_at=item.published_at,
                    collected_at=item.collected_at,
                    source_id=item.source_id,
                    source_name=item.source_name,
                    category_id=item.category_id,
                    category_name=item.category_name,
                    keywords=tuple(keywords.get(item.version_id, ())),
                )
                for item in items
            ]
        finally:
            connection.close()

    def count_scope_versions(
        self, since: int, until: int, source_id: str | None, category_id: str | None
    ) -> int:
        where, args = self._scope_filters(source_id, category_id)
        connection = self.database.connect()
        try:
            row = connection.execute(
                f"SELECT COUNT(*) AS n FROM ({_SCOPE}{where})", [since, until, *args]
            ).fetchone()
            return int(row["n"])
        finally:
            connection.close()

    @staticmethod
    def _scope_filters(source_id: str | None, category_id: str | None) -> tuple[str, list[object]]:
        clauses: list[str] = []
        args: list[object] = []
        if source_id:
            clauses.append("AND m.source_id = ?")
            args.append(source_id)
        if category_id:
            clauses.append("AND s.category_id = ?")
            args.append(category_id)
        return "".join(f"\n  {clause}" for clause in clauses), args

    @staticmethod
    def _keywords(
        connection: sqlite3.Connection, version_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        if not version_ids:
            return {}
        placeholders = ",".join("?" for _ in version_ids)
        result: dict[str, list[str]] = {}
        for row in connection.execute(
            _KEYWORDS.format(placeholders=placeholders), list(version_ids)
        ).fetchall():
            result.setdefault(str(row["version_id"]), []).append(str(row["keyword_text"]))
        return result

    @staticmethod
    def _input(row: sqlite3.Row) -> DiscoveryInputVersion:
        return DiscoveryInputVersion(
            message_id=str(row["message_id"]),
            version_id=str(row["version_id"]),
            version_number=int(row["version_number"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            content=str(row["content"]),
            url=str(row["url"]),
            published_at=int(row["published_at"]) if row["published_at"] is not None else None,
            collected_at=int(row["collected_at"]),
            source_id=str(row["source_id"]),
            source_name=str(row["source_name"]),
            category_id=str(row["category_id"]),
            category_name=str(row["category_name"]),
        )


class SQLiteDiscoveryRepository:
    """发现批次与候选；候选替换与租约校验在同一个事务里完成。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, batch: DiscoveryBatch) -> DiscoveryBatch:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO discovery_batches(
                    id, window_start, window_end, category_id, source_id, input_fingerprint,
                    idempotency_key, status, task_id, source_label, message_count,
                    candidate_count, duplicate_members, out_of_range, dropped_members,
                    dropped_candidates, error_code, error_message, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    batch.id,
                    batch.window_start,
                    batch.window_end,
                    batch.category_id,
                    batch.source_id,
                    batch.input_fingerprint,
                    batch.idempotency_key,
                    batch.status,
                    batch.task_id,
                    batch.source_label,
                    batch.message_count,
                    batch.candidate_count,
                    batch.duplicate_members,
                    batch.out_of_range,
                    batch.dropped_members,
                    batch.dropped_candidates,
                    batch.error_code,
                    batch.error_message,
                    batch.created_at,
                    batch.updated_at,
                ),
            )
        return batch

    def get(self, batch_id: str) -> DiscoveryBatch | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM discovery_batches WHERE id=?", (batch_id,)
            ).fetchone()
            return _batch(row) if row else None
        finally:
            connection.close()

    def get_by_idempotency(self, key: str) -> DiscoveryBatch | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM discovery_batches WHERE idempotency_key=?", (key,)
            ).fetchone()
            return _batch(row) if row else None
        finally:
            connection.close()

    def list(
        self, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[DiscoveryBatch], int]:
        where = "WHERE status = ?" if status else ""
        args: list[object] = [status] if status else []
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM discovery_batches {where}"
                " ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                [*args, max(1, limit), max(0, offset)],
            ).fetchall()
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS n FROM discovery_batches {where}", args
                ).fetchone()["n"]
            )
            return [_batch(row) for row in rows], total
        finally:
            connection.close()

    def update(self, batch: DiscoveryBatch, lease: TaskLease | None = None) -> DiscoveryBatch:
        """整体更新批次；传租约时在同一事务里校验，晚到的 worker 不能覆盖结果。"""

        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            connection.execute(
                """
                UPDATE discovery_batches SET
                    status=?, task_id=?, source_label=?, message_count=?, candidate_count=?,
                    duplicate_members=?, out_of_range=?, dropped_members=?, dropped_candidates=?,
                    error_code=?, error_message=?, updated_at=?
                WHERE id=?
                """,
                (
                    batch.status,
                    batch.task_id,
                    batch.source_label,
                    batch.message_count,
                    batch.candidate_count,
                    batch.duplicate_members,
                    batch.out_of_range,
                    batch.dropped_members,
                    batch.dropped_candidates,
                    batch.error_code,
                    batch.error_message,
                    batch.updated_at,
                    batch.id,
                ),
            )
        return batch

    def replace_candidates(
        self,
        batch_id: str,
        candidates: Sequence[DiscoveryCandidate],
        lease: TaskLease | None = None,
    ) -> int:
        """替换该批次的候选；同一批次重复执行时不会叠出两份。

        上一次尝试留下的自动线索若无人引用（写作项目），一并清掉，避免脏数据堆积；
        已被选中的候选及其线索保留——用户已经开始的写作不能被重试删除。
        """

        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            previous = [
                str(row["lead_id"])
                for row in connection.execute(
                    "SELECT lead_id FROM discovery_candidates WHERE batch_id=?", (batch_id,)
                ).fetchall()
            ]
            connection.execute("DELETE FROM discovery_candidates WHERE batch_id=?", (batch_id,))
            self._drop_orphan_leads(connection, previous)
            connection.executemany(
                """
                INSERT INTO discovery_candidates(
                    id, batch_id, lead_id, rank, title, summary, event_time, score,
                    reasons_json, member_count, dropped_members, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        candidate.id,
                        candidate.batch_id,
                        candidate.lead_id,
                        candidate.rank,
                        candidate.title,
                        candidate.summary,
                        candidate.event_time,
                        candidate.score,
                        _reasons_json(candidate.reasons),
                        candidate.member_count,
                        candidate.dropped_members,
                        candidate.created_at,
                    )
                    for candidate in candidates
                ],
            )
        return len(candidates)

    def candidates(self, batch_id: str) -> list[DiscoveryCandidate]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM discovery_candidates WHERE batch_id=? ORDER BY rank, id",
                (batch_id,),
            ).fetchall()
            return [_candidate(row) for row in rows]
        finally:
            connection.close()

    def candidate(self, candidate_id: str) -> DiscoveryCandidate | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM discovery_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            return _candidate(row) if row else None
        finally:
            connection.close()

    def delete(self, batch_id: str) -> int:
        """删除批次（候选级联）与其自动线索；已被写作项目引用的线索保留。"""

        with self.database.transaction() as connection:
            lead_ids = [
                str(row["lead_id"])
                for row in connection.execute(
                    "SELECT lead_id FROM discovery_candidates WHERE batch_id=?", (batch_id,)
                ).fetchall()
            ]
            cursor = connection.execute("DELETE FROM discovery_batches WHERE id=?", (batch_id,))
            deleted = cursor.rowcount if cursor.rowcount > 0 else 0
            if deleted:
                self._drop_orphan_leads(connection, lead_ids)
        return deleted

    @staticmethod
    def _drop_orphan_leads(connection: sqlite3.Connection, lead_ids: Sequence[str]) -> None:
        """删除不再被候选或写作项目引用的自动线索（人工线索不动）。"""

        for lead_id in lead_ids:
            connection.execute(
                """
                DELETE FROM leads WHERE id = ? AND kind = 'discovery'
                  AND NOT EXISTS (SELECT 1 FROM writing_projects p WHERE p.lead_id = leads.id)
                """,
                (lead_id,),
            )


def _reasons_json(reasons: Sequence[RecommendationReason]) -> str:
    return json.dumps(
        [{"kind": reason.kind, "text": reason.text} for reason in reasons], ensure_ascii=False
    )


class SQLiteWritingProjectRepository:
    """写作项目：候选 → 项目一一对应，重复选择返回既有项目。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, project: WritingProject) -> WritingProject:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO writing_projects(id, lead_id, candidate_id, title, status, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(candidate_id) WHERE candidate_id IS NOT NULL DO NOTHING
                """,
                (
                    project.id,
                    project.lead_id,
                    project.candidate_id,
                    project.title,
                    project.status,
                    project.created_at,
                    project.updated_at,
                ),
            )
        if project.candidate_id:
            existing = self.get_by_candidate(project.candidate_id)
            if existing is not None:
                return existing
        return self.get(project.id) or project

    def get(self, project_id: str) -> WritingProject | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM writing_projects WHERE id=?", (project_id,)
            ).fetchone()
            return _project(row) if row else None
        finally:
            connection.close()

    def get_by_candidate(self, candidate_id: str) -> WritingProject | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM writing_projects WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            return _project(row) if row else None
        finally:
            connection.close()

    def list(
        self, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[WritingProject], int]:
        where = "WHERE status = ?" if status else ""
        args: list[object] = [status] if status else []
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM writing_projects {where}"
                " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
                [*args, max(1, limit), max(0, offset)],
            ).fetchall()
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS n FROM writing_projects {where}", args
                ).fetchone()["n"]
            )
            return [_project(row) for row in rows], total
        finally:
            connection.close()

    def set_status(self, project_id: str, status: str, timestamp: int) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE writing_projects SET status=?, updated_at=? WHERE id=?",
                (status, timestamp, project_id),
            )
            return cursor.rowcount if cursor.rowcount > 0 else 0

    def set_materials_summary(
        self,
        project_id: str,
        status: str,
        summary_json: str,
        timestamp: int,
        lease: TaskLease | None = None,
    ) -> int:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            cursor = connection.execute(
                "UPDATE writing_projects SET status=?, materials_summary_json=?, updated_at=?"
                " WHERE id=?",
                (status, summary_json, timestamp, project_id),
            )
            return cursor.rowcount if cursor.rowcount > 0 else 0
