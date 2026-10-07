"""任务 SQLite 仓储。"""

from __future__ import annotations

import sqlite3

from rss_v2.adapters.sqlite.common import dumps, loads, now
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import Task, TaskStatus, TaskType


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        task_type=TaskType(row["task_type"]),
        idempotency_key=row["idempotency_key"],
        status=TaskStatus(row["status"]),
        attempts=row["attempts"],
        lease_until=row["lease_until"],
        input_version_id=row["input_version_id"],
        output_version_id=row["output_version_id"],
        payload=loads(row["payload_json"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SQLiteTaskRepository:
    """支持幂等创建、租约领取和恢复的任务仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, task: Task) -> Task:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,payload_json,error_code,error_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task.id,
                    task.task_type.value,
                    task.idempotency_key,
                    task.status.value,
                    task.attempts,
                    task.lease_until,
                    task.input_version_id,
                    task.output_version_id,
                    dumps(task.payload),
                    task.error_code,
                    task.error_message,
                    task.created_at,
                    task.updated_at,
                ),
            )
        return self.get(task.id) or task

    def get(self, task_id: str) -> Task | None:
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return _task(row) if row else None
        finally:
            connection.close()

    def get_by_idempotency(self, key: str) -> Task | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?", (key,)
            ).fetchone()
            return _task(row) if row else None
        finally:
            connection.close()

    def claim_next(self, task_types: list[str], now: int, lease_seconds: int) -> Task | None:
        if not task_types:
            return None
        placeholders = ",".join("?" for _ in task_types)
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE task_type IN ({placeholders})
                  AND (status = 'queued' OR (status = 'running' AND lease_until < ?))
                ORDER BY created_at
                LIMIT 1
                """,
                (*task_types, now),
            ).fetchone()
            if row is None:
                return None
            lease_until = now + lease_seconds
            connection.execute(
                "UPDATE tasks SET status='running', attempts=attempts+1, lease_until=?, updated_at=?, error_code=NULL, error_message=NULL WHERE id=?",
                (lease_until, now, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (row["id"],)
            ).fetchone()
            return _task(claimed) if claimed else None

    def complete(self, task_id: str, output_version_id: str | None = None) -> Task:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET status='succeeded', lease_until=NULL, output_version_id=?, updated_at=? WHERE id=?",
                (output_version_id, now(), task_id),
            )
        result = self.get(task_id)
        if result is None:
            raise KeyError(task_id)
        return result

    def fail(self, task_id: str, error_code: str, error_message: str, retryable: bool) -> Task:
        status = TaskStatus.QUEUED.value if retryable else TaskStatus.FAILED.value
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET status=?, lease_until=NULL, error_code=?, error_message=?, updated_at=? WHERE id=?",
                (status, error_code, error_message[:1000], now(), task_id),
            )
        result = self.get(task_id)
        if result is None:
            raise KeyError(task_id)
        return result

    def reclaim_expired(self, now: int) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='queued', lease_until=NULL, updated_at=? WHERE status='running' AND lease_until < ?",
                (now, now),
            )
            return cursor.rowcount
