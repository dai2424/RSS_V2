"""translations SQLite 仓储。"""

from __future__ import annotations

import sqlite3

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import (
    Translation,
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
