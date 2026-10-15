"""资料快照的 SQLite 仓储。

写入按内容指纹去重（部分唯一索引 `(project_id, content_hash)`），重复读取不会产生
第二行；"预算耗尽未读取"的占位行单独清理，重跑时重新评估。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.lease import ensure_lease
from rss_v2.domain import BUDGET_EXHAUSTED, Material, TaskLease


def _material(row: sqlite3.Row) -> Material:
    return Material(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        url=str(row["url"]),
        final_url=str(row["final_url"]),
        title=str(row["title"]),
        publisher=str(row["publisher"]),
        published_at=int(row["published_at"]) if row["published_at"] is not None else None,
        fetched_at=int(row["fetched_at"]),
        content_text=str(row["content_text"]),
        content_hash=str(row["content_hash"]),
        byte_size=int(row["byte_size"]),
        truncated=bool(row["truncated"]),
        message_version_id=(
            str(row["message_version_id"]) if row["message_version_id"] is not None else None
        ),
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        error_code=str(row["error_code"]),
        error_message=str(row["error_message"]),
        created_at=int(row["created_at"]),
    )


#: 列表里不返回正文：正文按需通过详情接口取，避免列表响应过大。
_LIST_COLUMNS = (
    "id, project_id, kind, status, url, final_url, title, publisher, published_at, fetched_at,"
    " '' AS content_text, content_hash, byte_size, truncated, message_version_id, task_id,"
    " error_code, error_message, created_at"
)


class SQLiteMaterialRepository:
    """资料行的持久化；写入去重、列表按获取顺序。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def add(self, material: Material, lease: TaskLease | None = None) -> Material | None:
        """写入一条资料；内容指纹重复时返回 None（重复读取不产生第二行）。"""

        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            cursor = connection.execute(
                """
                INSERT INTO project_materials(
                    id, project_id, kind, status, url, final_url, title, publisher, published_at,
                    fetched_at, content_text, content_hash, byte_size, truncated,
                    message_version_id, task_id, error_code, error_message, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, content_hash) WHERE content_hash <> '' DO NOTHING
                """,
                (
                    material.id,
                    material.project_id,
                    material.kind,
                    material.status,
                    material.url,
                    material.final_url,
                    material.title,
                    material.publisher,
                    material.published_at,
                    material.fetched_at,
                    material.content_text,
                    material.content_hash,
                    material.byte_size,
                    1 if material.truncated else 0,
                    material.message_version_id,
                    material.task_id,
                    material.error_code,
                    material.error_message,
                    material.created_at,
                ),
            )
            written = cursor.rowcount if cursor.rowcount > 0 else 0
        return material if written else None

    def add_all(self, materials: Sequence[Material], lease: TaskLease | None = None) -> int:
        added = 0
        for material in materials:
            if self.add(material, lease) is not None:
                added += 1
        return added

    def list(self, project_id: str) -> list[Material]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_LIST_COLUMNS} FROM project_materials WHERE project_id=?"
                # 同一轮里多条资料的 fetched_at/created_at 相同：用 rowid 保证按插入顺序稳定。
                " ORDER BY fetched_at, created_at, rowid",
                (project_id,),
            ).fetchall()
            return [_material(row) for row in rows]
        finally:
            connection.close()

    def get(self, material_id: str) -> Material | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM project_materials WHERE id=?", (material_id,)
            ).fetchone()
            return _material(row) if row else None
        finally:
            connection.close()

    def by_url(self, project_id: str, url: str) -> Material | None:
        if not url:
            return None
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM project_materials WHERE project_id=? AND (url=? OR final_url=?)"
                " LIMIT 1",
                (project_id, url, url),
            ).fetchone()
            return _material(row) if row else None
        finally:
            connection.close()

    def clear_budget_skips(self, project_id: str, lease: TaskLease | None = None) -> int:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            cursor = connection.execute(
                "DELETE FROM project_materials WHERE project_id=? AND error_code=?",
                (project_id, BUDGET_EXHAUSTED),
            )
            return cursor.rowcount if cursor.rowcount > 0 else 0

    def count(self, project_id: str) -> dict[str, int]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM project_materials WHERE project_id=?"
                " GROUP BY status",
                (project_id,),
            ).fetchall()
            counts = {str(row["status"]): int(row["n"]) for row in rows}
        finally:
            connection.close()
        counts["total"] = sum(counts.values())
        return counts

    def delete_for_project(self, project_id: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM project_materials WHERE project_id=?", (project_id,)
            )
            return cursor.rowcount if cursor.rowcount > 0 else 0
