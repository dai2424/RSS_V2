"""messages SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite import keyword_aliases as keyword_repository
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_sql import (
    CURRENT_ENRICHMENT,
    ESCAPE,
    LATEST_VERSION,
    RESOLVED_KEY,
    like_pattern,
    prefix_pattern,
)
from rss_v2.domain import (
    Message,
    MessageVersion,
    SourceLanguage,
    normalize_keyword,
)


def _message(row: sqlite3.Row) -> Message:
    return Message(
        row["id"], row["source_id"], row["external_id"], row["created_at"], row["updated_at"]
    )


def _version(row: sqlite3.Row) -> MessageVersion:
    return MessageVersion(
        id=row["id"],
        message_id=row["message_id"],
        version_number=row["version_number"],
        title=row["title"],
        summary=row["summary"],
        content=row["content"],
        url=row["url"],
        published_at=row["published_at"],
        collected_at=row["collected_at"],
        language=SourceLanguage(row["language"]),
        content_hash=row["content_hash"],
    )


#: 列表与计数共用的数据来源。三个 LEFT JOIN 都按主键取「每消息一行」，不会放大行数：
#: 因此 COUNT(*) 就是消息条数，与列表翻页看到的条数同源。
LIST_SOURCE = f"""
FROM messages m
LEFT JOIN message_versions v ON v.id = {LATEST_VERSION}
LEFT JOIN translations t ON t.id = (
    SELECT t2.id FROM translations t2
    WHERE t2.message_version_id = v.id AND t2.status = 'succeeded'
    ORDER BY t2.updated_at DESC LIMIT 1
)
LEFT JOIN message_enrichments e ON e.id = {CURRENT_ENRICHMENT}
"""


class SQLiteMessageRepository:
    """消息和版本仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def upsert_message(self, message: Message) -> Message:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO messages(id,source_id,external_id,created_at,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(source_id,external_id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (
                    message.id,
                    message.source_id,
                    message.external_id,
                    message.created_at,
                    message.updated_at,
                ),
            )
        existing = self.find_by_external_id(message.source_id, message.external_id)
        if existing is None:
            raise RuntimeError("消息写入后无法读取")
        return existing

    def get_message(self, message_id: str) -> Message | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            return _message(row) if row else None
        finally:
            connection.close()

    def find_by_external_id(self, source_id: str, external_id: str) -> Message | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM messages WHERE source_id = ? AND external_id = ?",
                (source_id, external_id),
            ).fetchone()
            return _message(row) if row else None
        finally:
            connection.close()

    def get_version(self, version_id: str) -> MessageVersion | None:
        """读取任务绑定的历史版本。"""
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM message_versions WHERE id=?", (version_id,)
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def find_by_url(self, source_id: str, url: str) -> Message | None:
        return self._find_version_match(source_id, "url", url)

    def find_by_hash(self, source_id: str, content_hash: str) -> Message | None:
        return self._find_version_match(source_id, "content_hash", content_hash)

    def _find_version_match(self, source_id: str, field: str, value: str) -> Message | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                f"SELECT m.* FROM messages m JOIN message_versions v ON v.message_id=m.id WHERE m.source_id=? AND v.{field}=? ORDER BY v.version_number DESC LIMIT 1",
                (source_id, value),
            ).fetchone()
            return _message(row) if row else None
        finally:
            connection.close()

    def latest_version(self, message_id: str) -> MessageVersion | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM message_versions WHERE message_id = ? ORDER BY version_number DESC LIMIT 1",
                (message_id,),
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def add_version(self, version: MessageVersion) -> MessageVersion:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO message_versions(
                    id,message_id,version_number,title,summary,content,url,published_at,
                    collected_at,language,content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version.id,
                    version.message_id,
                    version.version_number,
                    version.title,
                    version.summary,
                    version.content,
                    version.url,
                    version.published_at,
                    version.collected_at,
                    version.language.value,
                    version.content_hash,
                ),
            )
        return version

    def list_messages(
        self,
        query: str | None = None,
        source_id: str | None = None,
        keyword: str | None = None,
        kind: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Message, MessageVersion | None]]:
        where, args = self._filters(query, source_id, keyword, kind, since, until)
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"""
                SELECT m.id AS message_id, m.source_id, m.external_id, m.created_at AS message_created_at,
                       m.updated_at AS message_updated_at, v.*
                {LIST_SOURCE}
                {where}
                ORDER BY COALESCE(v.published_at, v.collected_at) DESC, m.id
                LIMIT ? OFFSET ?
                """,
                (*args, max(1, min(limit, 100)), max(0, offset)),
            ).fetchall()
            result: list[tuple[Message, MessageVersion | None]] = []
            for row in rows:
                message = Message(
                    row["message_id"],
                    row["source_id"],
                    row["external_id"],
                    row["message_created_at"],
                    row["message_updated_at"],
                )
                version = _version(row) if row["id"] is not None else None
                result.append((message, version))
            return result
        finally:
            connection.close()

    def count_messages(
        self,
        query: str | None = None,
        source_id: str | None = None,
        keyword: str | None = None,
        kind: str | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> int:
        """与 list_messages 同一套过滤条件、同一套连接的总数，供列表分页使用。"""

        where, args = self._filters(query, source_id, keyword, kind, since, until)
        connection = self.database.connect()
        try:
            row = connection.execute(f"SELECT COUNT(*) {LIST_SOURCE} {where}", args).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def _filters(
        self,
        query: str | None,
        source_id: str | None,
        keyword: str | None,
        kind: str | None,
        since: int | None,
        until: int | None,
    ) -> tuple[str, list[Any]]:
        """列表与计数共用的 WHERE；两处必须同源，否则总数与页面内容对不上。"""

        clauses: list[str] = []
        args: list[Any] = []
        if source_id:
            clauses.append("m.source_id = ?")
            args.append(source_id)
        if query:
            # 全文兜底：原文、最新译文、最新加工结果的标题与摘要，以及关键词的匹配键。
            clauses.append(
                f"(v.title LIKE ? ESCAPE '{ESCAPE}' OR v.summary LIKE ? ESCAPE '{ESCAPE}'"
                f" OR v.content LIKE ? ESCAPE '{ESCAPE}'"
                f" OR t.title LIKE ? ESCAPE '{ESCAPE}' OR t.summary LIKE ? ESCAPE '{ESCAPE}'"
                f" OR t.content LIKE ? ESCAPE '{ESCAPE}'"
                f" OR e.title LIKE ? ESCAPE '{ESCAPE}' OR e.summary LIKE ? ESCAPE '{ESCAPE}'"
                " OR EXISTS (SELECT 1 FROM enrichment_keywords k"
                f" WHERE k.enrichment_id = e.id AND k.normalized LIKE ? ESCAPE '{ESCAPE}'))"
            )
            pattern = like_pattern(query)
            args.extend([pattern] * 9)
        if keyword:
            # 输入先解析到规范词：搜索被合并掉的写法时，命中它并入的那个词条。
            resolved = keyword_repository.resolve_key(self.database, normalize_keyword(keyword))
            if resolved:
                kind_clause = " AND k.kind = ?" if kind else ""
                clauses.append(
                    "EXISTS (SELECT 1 FROM enrichment_keywords k"
                    " LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized"
                    f" WHERE k.enrichment_id = {CURRENT_ENRICHMENT}"
                    f" AND ({RESOLVED_KEY} = ? OR {RESOLVED_KEY} LIKE ? ESCAPE '{ESCAPE}')"
                    f"{kind_clause})"
                )
                args.extend([resolved, prefix_pattern(resolved)])
                if kind:
                    args.append(kind)
            else:
                # 输入没有可匹配的字面内容（例如全是标点）：明确返回空集，而不是忽略该条件。
                clauses.append("0")
        if since is not None:
            clauses.append("COALESCE(v.published_at, v.collected_at) >= ?")
            args.append(since)
        if until is not None:
            clauses.append("COALESCE(v.published_at, v.collected_at) <= ?")
            args.append(until)
        return (f"WHERE {' AND '.join(clauses)}" if clauses else "", args)

    def versions(self, message_id: str) -> list[MessageVersion]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM message_versions WHERE message_id = ? ORDER BY version_number DESC",
                (message_id,),
            ).fetchall()
            return [_version(row) for row in rows]
        finally:
            connection.close()

    def count_for_source(self, source_id: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE source_id = ?", (source_id,)
            ).fetchone()
            return int(row[0])
        finally:
            connection.close()
