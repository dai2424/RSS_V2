"""消息、版本、采集运行和翻译 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite.common import dumps, loads
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import (
    CollectionRun,
    Message,
    MessageVersion,
    SourceLanguage,
    TaskStatus,
    Translation,
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


def _translation(row: sqlite3.Row) -> Translation:
    return Translation(
        id=row["id"],
        message_version_id=row["message_version_id"],
        status=row["status"],
        title=row["title"],
        summary=row["summary"],
        content=row["content"],
        provider_id=row["provider_id"],
        key_ref=row["key_ref"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        task_id=row["task_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run(row: sqlite3.Row) -> CollectionRun:
    source_ids = loads(row["source_ids_json"]).get("source_ids", [])
    return CollectionRun(
        id=row["id"],
        source_ids=[str(source_id) for source_id in source_ids],
        status=TaskStatus(row["status"]),
        requested_at=row["requested_at"],
        completed_at=row["completed_at"],
        created_count=row["created_count"],
        updated_count=row["updated_count"],
        skipped_count=row["skipped_count"],
        failed_count=row["failed_count"],
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


class SQLiteCollectionRunRepository:
    """采集运行仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, run: CollectionRun) -> CollectionRun:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO collection_runs(id,source_ids_json,status,requested_at,completed_at,created_count,updated_count,skipped_count,failed_count) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    dumps({"source_ids": run.source_ids}),
                    run.status.value,
                    run.requested_at,
                    run.completed_at,
                    run.created_count,
                    run.updated_count,
                    run.skipped_count,
                    run.failed_count,
                ),
            )
        return run

    def get(self, run_id: str) -> CollectionRun | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM collection_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return _run(row) if row else None
        finally:
            connection.close()

    def update_counts(self, run_id: str, changes: dict[str, Any]) -> CollectionRun:
        allowed = {
            "status",
            "completed_at",
            "created_count",
            "updated_count",
            "skipped_count",
            "failed_count",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if "status" in values and isinstance(values["status"], TaskStatus):
            values["status"] = values["status"].value
        if not values:
            result = self.get(run_id)
            if result is None:
                raise KeyError(run_id)
            return result
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE collection_runs SET {assignments} WHERE id = ?", (*values.values(), run_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(run_id)
        result = self.get(run_id)
        if result is None:
            raise KeyError(run_id)
        return result


class SQLiteTranslationRepository:
    """翻译仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get_for_version(
        self, version_id: str, prompt_version: str, model: str
    ) -> Translation | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM translations WHERE message_version_id=? AND prompt_version=? AND model=?",
                (version_id, prompt_version, model),
            ).fetchone()
            return _translation(row) if row else None
        finally:
            connection.close()

    def list_for_version(self, version_id: str) -> list[Translation]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM translations WHERE message_version_id=? ORDER BY updated_at DESC",
                (version_id,),
            ).fetchall()
            return [_translation(row) for row in rows]
        finally:
            connection.close()

    def save(self, translation: Translation) -> Translation:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO translations(id,message_version_id,status,title,summary,content,provider_id,key_ref,model,prompt_version,task_id,error_code,error_message,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(message_version_id,prompt_version,model) DO UPDATE SET
                    status=excluded.status,title=excluded.title,summary=excluded.summary,content=excluded.content,
                    provider_id=excluded.provider_id,key_ref=excluded.key_ref,task_id=excluded.task_id,
                    error_code=excluded.error_code,error_message=excluded.error_message,updated_at=excluded.updated_at
                """,
                (
                    translation.id,
                    translation.message_version_id,
                    translation.status,
                    translation.title,
                    translation.summary,
                    translation.content,
                    translation.provider_id,
                    translation.key_ref,
                    translation.model,
                    translation.prompt_version,
                    translation.task_id,
                    translation.error_code,
                    translation.error_message,
                    translation.created_at,
                    translation.updated_at,
                ),
            )
        result = self.get_for_version(
            translation.message_version_id, translation.prompt_version, translation.model or ""
        )
        if result is None:
            raise RuntimeError("翻译写入后无法读取")
        return result

    def get(self, translation_id: str) -> Translation | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM translations WHERE id = ?", (translation_id,)
            ).fetchone()
            return _translation(row) if row else None
        finally:
            connection.close()
