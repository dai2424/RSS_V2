"""合并建议的存储：批次台账、建议本身与两种处置。

建议是"给人看的候选"，不是合并结果：采纳只是标记并把 `merge_id` 记下来，真正的合并
仍走 `keyword_aliases.merge`（含影响面预览与撤销）。成员集合指纹唯一，因此同一组不会被
重复建议；已忽略的组保留记录，也就不会再次出现。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import DomainError, Keyword, KeywordKind, MergeSuggestion


class SQLiteSuggestionRepository:
    """合并建议仓储：只做转发，SQL 都在本模块的函数里。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def existing_fingerprints(self) -> set[str]:
        """已经建议过的成员集合指纹。"""

        return existing_fingerprints(self.database)

    def batch_result(self, fingerprint: str, offset: int) -> tuple[int, int] | None:
        """已生成过的批次返回 (组数, 丢弃数)。"""

        return batch_result(self.database, fingerprint, offset)

    def save_batch(
        self,
        fingerprint: str,
        offset: int,
        suggestions: list[MergeSuggestion],
        dropped: int,
        timestamp: int,
    ) -> list[MergeSuggestion]:
        """批次台账与建议行同事务写入。"""

        return save_batch(self.database, fingerprint, offset, suggestions, dropped, timestamp)

    def list(self, status: str | None, limit: int) -> list[MergeSuggestion]:
        """建议列表。"""

        return list_suggestions(self.database, status, limit)

    def pending_count(self) -> int:
        """待处理建议数。"""

        return pending_count(self.database)

    def dismiss(self, suggestion_id: str, timestamp: int) -> None:
        """忽略一条建议。"""

        dismiss(self.database, suggestion_id, timestamp)

    def accept(self, suggestion_id: str, merge_id: str, timestamp: int) -> None:
        """标记建议已采纳。"""

        accept(self.database, suggestion_id, merge_id, timestamp)


def existing_fingerprints(database: SQLiteDatabase) -> set[str]:
    """已经建议过的成员集合指纹（含已忽略与已采纳），用于去重。"""

    connection = database.connect()
    try:
        rows = connection.execute("SELECT fingerprint FROM keyword_suggestions").fetchall()
        return {str(row["fingerprint"]) for row in rows}
    finally:
        connection.close()


def batch_result(database: SQLiteDatabase, fingerprint: str, offset: int) -> tuple[int, int] | None:
    """已生成过的批次返回 (组数, 丢弃数)；没生成过返回 None。"""

    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT groups, dropped FROM keyword_suggestion_batches"
            " WHERE batch_fingerprint=? AND batch_offset=?",
            (fingerprint, offset),
        ).fetchone()
        return (int(row["groups"]), int(row["dropped"])) if row else None
    finally:
        connection.close()


def save_batch(
    database: SQLiteDatabase,
    fingerprint: str,
    offset: int,
    suggestions: list[MergeSuggestion],
    dropped: int,
    timestamp: int,
) -> list[MergeSuggestion]:
    """批次台账与建议行同事务写入，返回落库后的建议。"""

    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO keyword_suggestion_batches(batch_fingerprint,batch_offset,groups,dropped,created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(batch_fingerprint,batch_offset) DO NOTHING
            """,
            (fingerprint, offset, len(suggestions), dropped, timestamp),
        )
        for item in suggestions:
            connection.execute(
                """
                INSERT INTO keyword_suggestions(id,members_json,target_key,target_text,rationale,
                    status,batch_offset,batch_fingerprint,fingerprint,merge_id,created_at,resolved_at)
                VALUES(?,?,?,?,?,'pending',?,?,?,NULL,?,NULL)
                """,
                (
                    item.id,
                    json.dumps(
                        [
                            {
                                "text": keyword.text,
                                "kind": keyword.kind.value,
                                "key": keyword.normalized,
                            }
                            for keyword in item.members
                        ],
                        ensure_ascii=False,
                    ),
                    item.target_key,
                    item.target_text,
                    item.rationale,
                    offset,
                    fingerprint,
                    item.id,
                    timestamp,
                ),
            )
    return suggestions


def list_suggestions(
    database: SQLiteDatabase, status: str | None, limit: int
) -> list[MergeSuggestion]:
    """建议列表；界面默认只看待处理的。"""

    connection = database.connect()
    try:
        if status:
            rows = connection.execute(
                "SELECT * FROM keyword_suggestions WHERE status=? ORDER BY created_at DESC, id LIMIT ?",
                (status, max(1, limit)),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM keyword_suggestions ORDER BY created_at DESC, id LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_suggestion(row) for row in rows]
    finally:
        connection.close()


def pending_count(database: SQLiteDatabase) -> int:
    """待处理建议数；页面与概览用它提示还有多少组没看。"""

    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM keyword_suggestions WHERE status='pending'"
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        connection.close()


def dismiss(database: SQLiteDatabase, suggestion_id: str, timestamp: int) -> None:
    """忽略一条建议；已处理的建议不再出现在待处理列表里。"""

    with database.transaction() as connection:
        _require_pending(connection, suggestion_id)
        connection.execute(
            "UPDATE keyword_suggestions SET status='dismissed', resolved_at=? WHERE id=?",
            (timestamp, suggestion_id),
        )


def accept(database: SQLiteDatabase, suggestion_id: str, merge_id: str, timestamp: int) -> None:
    """标记建议已采纳并记下合并记录；合并本身由既有端点完成。"""

    with database.transaction() as connection:
        _require_pending(connection, suggestion_id)
        merged = connection.execute(
            "SELECT 1 FROM keyword_merges WHERE id=?", (merge_id,)
        ).fetchone()
        if merged is None:
            raise DomainError("keyword_merge_not_found", "合并记录不存在")
        connection.execute(
            """
            UPDATE keyword_suggestions
            SET status='accepted', resolved_at=?, merge_id=?
            WHERE id=?
            """,
            (timestamp, merge_id, suggestion_id),
        )


def _require_pending(connection: sqlite3.Connection, suggestion_id: str) -> None:
    row = connection.execute(
        "SELECT status FROM keyword_suggestions WHERE id=?", (suggestion_id,)
    ).fetchone()
    if row is None:
        raise DomainError("keyword_suggestion_not_found", "合并建议不存在")
    if str(row["status"]) != "pending":
        raise DomainError("keyword_suggestion_resolved", "这条建议已经处理过了")


def _suggestion(row: sqlite3.Row) -> MergeSuggestion:
    raw: object = json.loads(str(row["members_json"]))
    members: list[Keyword] = []
    for item in cast(list[Any], raw) if isinstance(raw, list) else []:
        entry = cast(dict[str, Any], item) if isinstance(item, dict) else {}
        text = str(entry.get("text") or "")
        if not text:
            continue
        # 展示用写法来自词表快照，匹配键用生成时记下的规范词键。
        members.append(Keyword(text=text, kind=KeywordKind(str(entry.get("kind") or "topic"))))
    return MergeSuggestion(
        id=str(row["id"]),
        members=tuple(members),
        target_key=str(row["target_key"]),
        target_text=str(row["target_text"]),
        rationale=str(row["rationale"]),
        status=str(row["status"]),
        batch_offset=int(row["batch_offset"]),
        created_at=int(row["created_at"]),
        resolved_at=row["resolved_at"],
        merge_id=row["merge_id"],
    )
