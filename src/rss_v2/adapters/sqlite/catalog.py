"""分类、来源和健康记录 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite.common import dumps, loads, new_id, now
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.source_deletion import delete_source
from rss_v2.domain import Category, HealthCheck, Source, SourceDeletion, SourceLanguage


def _category(row: sqlite3.Row) -> Category:
    return Category(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        is_builtin=bool(row["is_builtin"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        platform=row["platform"],
        language=SourceLanguage(row["language"]),
        category_id=row["category_id"],
        enabled=bool(row["enabled"]),
        time_offset_minutes=row["time_offset_minutes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        feed_title=row["feed_title"],
        feed_link=row["feed_link"],
        feed_description=row["feed_description"],
        feed_language=row["feed_language"],
        feed_author=row["feed_author"],
        feed_updated_at=row["feed_updated_at"],
        source_type=row["source_type"],
        metadata=loads(row["metadata_json"]),
    )


def _health(row: sqlite3.Row) -> HealthCheck:
    return HealthCheck(
        id=row["id"],
        source_id=row["source_id"],
        checked_at=row["checked_at"],
        http_status=row["http_status"],
        latency_ms=row["latency_ms"],
        parse_success=bool(row["parse_success"]),
        entry_count=row["entry_count"],
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


class SQLiteCategoryRepository:
    """分类仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def list(self, include_inactive: bool = False) -> list[Category]:
        connection = self.database.connect()
        try:
            sql = "SELECT * FROM categories"
            if not include_inactive:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY is_builtin DESC, name"
            return [_category(row) for row in connection.execute(sql).fetchall()]
        finally:
            connection.close()

    def get(self, category_id: str) -> Category | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
            return _category(row) if row else None
        finally:
            connection.close()

    def create(self, name: str, slug: str, is_builtin: bool = False) -> Category:
        category = Category(
            new_id(), name.strip(), slug.strip().lower(), is_builtin, True, now(), now()
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO categories(id,name,slug,is_builtin,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    category.id,
                    category.name,
                    category.slug,
                    int(category.is_builtin),
                    1,
                    category.created_at,
                    category.updated_at,
                ),
            )
        return category


class SQLiteSourceRepository:
    """来源仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    @staticmethod
    def _filters(
        query: str | None,
        category_id: str | None,
        enabled: bool | None,
    ) -> tuple[str, list[Any]]:
        """构造 list 与 count 共用的过滤条件，返回 WHERE 子句和绑定参数。"""

        clauses: list[str] = []
        args: list[Any] = []
        if query:
            clauses.append("(s.name LIKE ? OR s.url LIKE ? OR s.platform LIKE ?)")
            pattern = f"%{query.strip()}%"
            args.extend([pattern, pattern, pattern])
        if category_id:
            clauses.append("s.category_id = ?")
            args.append(category_id)
        if enabled is not None:
            clauses.append("s.enabled = ?")
            args.append(int(enabled))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, args

    def list(
        self,
        query: str | None = None,
        category_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Source]:
        where, args = self._filters(query, category_id, enabled)
        connection = self.database.connect()
        try:
            # 按创建时间倒序并用 rowid 决胜：编辑、启停不改变行位置，分页顺序稳定。
            rows = connection.execute(
                f"SELECT s.* FROM rss_sources s{where} "
                "ORDER BY s.created_at DESC, s.rowid DESC LIMIT ? OFFSET ?",
                (*args, max(1, min(limit, 100)), max(0, offset)),
            ).fetchall()
            return [_source(row) for row in rows]
        finally:
            connection.close()

    def count(
        self,
        query: str | None = None,
        category_id: str | None = None,
        enabled: bool | None = None,
    ) -> int:
        where, args = self._filters(query, category_id, enabled)
        connection = self.database.connect()
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM rss_sources s{where}", args).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def delete(self, source_id: str) -> SourceDeletion:
        """级联删除来源；具体清理顺序见 source_deletion 模块。"""

        return delete_source(self.database, source_id)

    def get(self, source_id: str) -> Source | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM rss_sources WHERE id = ?", (source_id,)
            ).fetchone()
            return _source(row) if row else None
        finally:
            connection.close()

    def create(self, source: Source) -> Source:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO rss_sources(
                    id,name,url,platform,language,category_id,enabled,time_offset_minutes,
                    feed_title,feed_link,feed_description,feed_language,feed_author,feed_updated_at,
                    source_type,metadata_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source.id,
                    source.name,
                    source.url,
                    source.platform,
                    source.language.value,
                    source.category_id,
                    int(source.enabled),
                    source.time_offset_minutes,
                    source.feed_title,
                    source.feed_link,
                    source.feed_description,
                    source.feed_language,
                    source.feed_author,
                    source.feed_updated_at,
                    source.source_type,
                    dumps(source.metadata),
                    source.created_at,
                    source.updated_at,
                ),
            )
        return source

    def update(self, source_id: str, changes: dict[str, Any]) -> Source:
        allowed = {
            "name",
            "url",
            "platform",
            "language",
            "category_id",
            "enabled",
            "time_offset_minutes",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if "language" in values and isinstance(values["language"], SourceLanguage):
            values["language"] = values["language"].value
        if not values:
            current = self.get(source_id)
            if current is None:
                raise KeyError(source_id)
            return current
        values["updated_at"] = now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE rss_sources SET {assignments} WHERE id = ?", (*values.values(), source_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(source_id)
        result = self.get(source_id)
        if result is None:
            raise KeyError(source_id)
        return result

    def save_feed_metadata(self, source_id: str, metadata: dict[str, Any]) -> Source:
        changes = {
            "feed_title": metadata.get("title"),
            "feed_link": metadata.get("link"),
            "feed_description": metadata.get("description"),
            "feed_language": metadata.get("language"),
            "feed_author": metadata.get("author"),
            "feed_updated_at": metadata.get("updated_at"),
            "metadata_json": dumps(metadata.get("extra", {})),
            "source_type": metadata.get("extra", {}).get("feed_type", "rss"),
            "updated_at": now(),
        }
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE rss_sources SET {assignments} WHERE id = ?", (*changes.values(), source_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(source_id)
        result = self.get(source_id)
        if result is None:
            raise KeyError(source_id)
        return result


class SQLiteHealthRepository:
    """健康记录仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def last_success_at(self, source_id: str) -> int | None:
        """最近一次成功检查的 UTC 秒，失败不覆盖该值。"""
        connection = self.database.connect()
        try:
            return connection.execute(
                "SELECT max(checked_at) FROM source_health_checks WHERE source_id=? AND parse_success=1",
                (source_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def add(self, check: HealthCheck) -> HealthCheck:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO source_health_checks(id,source_id,checked_at,http_status,latency_ms,parse_success,entry_count,error_code,error_message) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    check.id,
                    check.source_id,
                    check.checked_at,
                    check.http_status,
                    check.latency_ms,
                    int(check.parse_success),
                    check.entry_count,
                    check.error_code,
                    check.error_message,
                ),
            )
        return check

    def list_for_source(self, source_id: str, limit: int = 20) -> list[HealthCheck]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM source_health_checks WHERE source_id = ? ORDER BY checked_at DESC LIMIT ?",
                (source_id, max(1, min(limit, 100))),
            ).fetchall()
            return [_health(row) for row in rows]
        finally:
            connection.close()
