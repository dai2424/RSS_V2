"""message_enrichments SQLite 仓储。"""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import DomainError, Enrichment


def _enrichment(row: sqlite3.Row) -> Enrichment:
    return Enrichment(
        id=row["id"],
        message_version_id=row["message_version_id"],
        status=row["status"],
        title=row["title"],
        summary=row["summary"],
        keywords=_keywords(row["keywords_json"]),
        provider_id=row["provider_id"],
        key_masked=row["key_masked"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        task_id=row["task_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _keywords(value: str) -> tuple[str, ...]:
    """关键词列只存本仓储写入的 JSON 数组；异常内容按空处理而不是让读取失败。"""

    try:
        parsed: object = json.loads(value)
    except ValueError:
        return ()
    if not isinstance(parsed, list):
        return ()
    # isinstance 已限定为列表，元素仍是任意 JSON 值，逐个转成字符串。
    items = cast(list[object], parsed)
    return tuple(str(item) for item in items)


class SQLiteEnrichmentRepository:
    """内容加工结果仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get_for_version(
        self, version_id: str, prompt_version: str, model: str
    ) -> Enrichment | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM message_enrichments"
                " WHERE message_version_id=? AND prompt_version=? AND model=?",
                (version_id, prompt_version, model),
            ).fetchone()
            return _enrichment(row) if row else None
        finally:
            connection.close()

    def list_for_version(self, version_id: str) -> list[Enrichment]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM message_enrichments WHERE message_version_id=?"
                " ORDER BY updated_at DESC",
                (version_id,),
            ).fetchall()
            return [_enrichment(row) for row in rows]
        finally:
            connection.close()

    def save(self, enrichment: Enrichment, lease_token: str | None = None) -> Enrichment:
        with self.database.transaction() as connection:
            # 与译文一致：同一写事务内校验领取者，过期 worker 不能覆盖新结果。
            if lease_token is not None:
                owned = connection.execute(
                    "SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=?",
                    (enrichment.task_id, lease_token),
                ).fetchone()
                if owned is None:
                    raise DomainError("task_lease_lost", "任务租约已被回收，当前结果不再写入")
            connection.execute(
                """
                INSERT INTO message_enrichments(id,message_version_id,status,title,summary,
                    keywords_json,provider_id,key_masked,model,prompt_version,task_id,
                    error_code,error_message,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(message_version_id,prompt_version,model) DO UPDATE SET
                    status=excluded.status,title=excluded.title,summary=excluded.summary,
                    keywords_json=excluded.keywords_json,provider_id=excluded.provider_id,
                    key_masked=excluded.key_masked,task_id=excluded.task_id,
                    error_code=excluded.error_code,error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    enrichment.id,
                    enrichment.message_version_id,
                    enrichment.status,
                    enrichment.title,
                    enrichment.summary,
                    json.dumps(list(enrichment.keywords), ensure_ascii=False),
                    enrichment.provider_id,
                    enrichment.key_masked,
                    enrichment.model,
                    enrichment.prompt_version,
                    enrichment.task_id,
                    enrichment.error_code,
                    enrichment.error_message,
                    enrichment.created_at,
                    enrichment.updated_at,
                ),
            )
        result = self.get_for_version(
            enrichment.message_version_id, enrichment.prompt_version, enrichment.model or ""
        )
        if result is None:
            raise RuntimeError("加工结果写入后无法读取")
        return result

    def get(self, enrichment_id: str) -> Enrichment | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM message_enrichments WHERE id = ?", (enrichment_id,)
            ).fetchone()
            return _enrichment(row) if row else None
        finally:
            connection.close()
