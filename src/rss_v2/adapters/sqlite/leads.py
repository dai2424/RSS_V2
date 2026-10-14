"""leads SQLite 仓储：线索本体与成员（消息版本）。

成员按发布时间（缺失时回落到采集时间）从早到晚返回——线索按时间阅读才顺；展示用的标题、来源与时间都从消息版本与来源表现取，
不冗余存储——消息被删掉时成员行随之级联消失（外键 ON DELETE CASCADE）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import Lead, LeadMember, LeadSummary


def _lead(row: sqlite3.Row) -> Lead:
    return Lead(
        id=str(row["id"]),
        title=str(row["title"]),
        note=str(row["note"]) if row["note"] is not None else None,
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _member(row: sqlite3.Row) -> LeadMember:
    return LeadMember(
        message_id=str(row["message_id"]),
        version_id=str(row["message_version_id"]),
        version_number=int(row["version_number"]),
        title=str(row["title"]),
        source_id=str(row["source_id"]),
        source_name=str(row["source_name"]),
        published_at=int(row["published_at"]) if row["published_at"] is not None else None,
        collected_at=int(row["collected_at"]),
        added_at=int(row["added_at"]),
    )


#: 成员行的取值：展示信息来自消息版本与来源，成员表只存"哪一版、什么时候加入"。
_MEMBERS = """
SELECT lm.message_version_id, lm.added_at, v.message_id, v.version_number, v.title,
       v.published_at, v.collected_at, s.id AS source_id, s.name AS source_name
FROM lead_messages lm
JOIN message_versions v ON v.id = lm.message_version_id
JOIN messages m ON m.id = v.message_id
JOIN rss_sources s ON s.id = m.source_id
WHERE lm.lead_id = ?
ORDER BY COALESCE(v.published_at, v.collected_at), lm.added_at, v.id
"""


class SQLiteLeadRepository:
    """线索的持久化；所有写操作都在事务内完成并同步 updated_at。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, lead: Lead, version_ids: Sequence[str]) -> Lead:
        """写入线索与初始成员；重复的版本 id 被忽略。"""

        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO leads(id,title,note,created_at,updated_at) VALUES(?,?,?,?,?)",
                (lead.id, lead.title, lead.note, lead.created_at, lead.updated_at),
            )
            connection.executemany(
                "INSERT INTO lead_messages(lead_id,message_version_id,added_at) VALUES(?,?,?)"
                " ON CONFLICT(lead_id,message_version_id) DO NOTHING",
                [(lead.id, version_id, lead.created_at) for version_id in version_ids],
            )
        return lead

    def get(self, lead_id: str) -> Lead | None:
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            return _lead(row) if row else None
        finally:
            connection.close()

    def list(self, limit: int, offset: int) -> tuple[list[LeadSummary], int]:
        """线索列表与总数；按最近成员变化时间倒序，成员条数一次查出来。"""

        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT l.*, COUNT(lm.message_version_id) AS members
                FROM leads l
                LEFT JOIN lead_messages lm ON lm.lead_id = l.id
                GROUP BY l.id
                ORDER BY l.updated_at DESC, l.id
                LIMIT ? OFFSET ?
                """,
                (max(1, limit), max(0, offset)),
            ).fetchall()
            total = int(connection.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"])
            return [LeadSummary(_lead(row), int(row["members"])) for row in rows], total
        finally:
            connection.close()

    def members(self, lead_id: str) -> list[LeadMember]:
        connection = self.database.connect()
        try:
            rows = connection.execute(_MEMBERS, (lead_id,)).fetchall()
            return [_member(row) for row in rows]
        finally:
            connection.close()

    def add_members(self, lead_id: str, version_ids: Sequence[str], timestamp: int) -> int:
        """追加成员并刷新 updated_at，返回实际新增的条数。"""

        added = 0
        with self.database.transaction() as connection:
            for version_id in version_ids:
                cursor = connection.execute(
                    "INSERT INTO lead_messages(lead_id,message_version_id,added_at)"
                    " VALUES(?,?,?) ON CONFLICT(lead_id,message_version_id) DO NOTHING",
                    (lead_id, version_id, timestamp),
                )
                added += cursor.rowcount if cursor.rowcount > 0 else 0
            if added:
                connection.execute("UPDATE leads SET updated_at=? WHERE id=?", (timestamp, lead_id))
        return added

    def remove_member(self, lead_id: str, version_id: str, timestamp: int) -> int:
        """移除一个成员并刷新 updated_at，返回删除行数。"""

        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM lead_messages WHERE lead_id=? AND message_version_id=?",
                (lead_id, version_id),
            )
            removed = cursor.rowcount if cursor.rowcount > 0 else 0
            if removed:
                connection.execute("UPDATE leads SET updated_at=? WHERE id=?", (timestamp, lead_id))
        return removed

    def delete(self, lead_id: str) -> int:
        """删除线索（成员随外键级联），返回删除行数。"""

        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM leads WHERE id=?", (lead_id,))
            return cursor.rowcount if cursor.rowcount > 0 else 0
