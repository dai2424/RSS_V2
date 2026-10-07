"""messages SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import (
    Message,
    MessageVersion,
    SourceLanguage,
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
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Message, MessageVersion | None]]:
        clauses: list[str] = []
        args: list[Any] = []
        if source_id:
            clauses.append("m.source_id = ?")
            args.append(source_id)
        if query:
            clauses.append("(v.title LIKE ? OR v.summary LIKE ? OR v.content LIKE ?)")
            pattern = f"%{query.strip()}%"
            args.extend([pattern, pattern, pattern])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"""
                SELECT m.id AS message_id, m.source_id, m.external_id, m.created_at AS message_created_at,
                       m.updated_at AS message_updated_at, v.*
                FROM messages m
                LEFT JOIN message_versions v ON v.id = (
                    SELECT v2.id FROM message_versions v2
                    WHERE v2.message_id = m.id ORDER BY v2.version_number DESC LIMIT 1
                )
                {where}
                ORDER BY m.updated_at DESC LIMIT ? OFFSET ?
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
