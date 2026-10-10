"""任务 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite.common import dumps, loads, new_id, now
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_sql import ESCAPE, like_pattern
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
        lease_token=row["lease_token"],
        available_at=row["available_at"],
    )


def insert_task(connection: sqlite3.Connection, task: Task) -> None:
    """事务内写入幂等任务，由调用者提交。"""
    connection.execute(
        "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,lease_until,input_version_id,output_version_id,payload_json,error_code,error_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
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


#: 列表与计数共用的来源：目标搜索要看消息标题与来源名，来源筛选对采集任务取
#: payload 里的来源、对消息任务取所属消息的来源。
TASK_SOURCE = """
FROM tasks t
LEFT JOIN message_versions v ON v.id = t.input_version_id
LEFT JOIN messages m ON m.id = v.message_id
LEFT JOIN rss_sources ms ON ms.id = m.source_id
LEFT JOIN rss_sources cs ON cs.id = json_extract(t.payload_json, '$.source_id')
"""

#: 任务归属的来源：采集任务看 payload，翻译与加工任务看消息所属来源。
_TASK_SOURCE_ID = (
    "CASE WHEN t.task_type = 'collect_source'"
    " THEN json_extract(t.payload_json, '$.source_id') ELSE m.source_id END"
)


def _filters(
    status: str | None,
    task_type: str | None,
    source_id: str | None,
    query: str | None,
    since: int | None,
    until: int | None,
) -> tuple[str, list[Any]]:
    """列表与计数共用的 WHERE；两处必须同源，否则总数与页面内容对不上。"""

    clauses: list[str] = []
    args: list[Any] = []
    if status:
        clauses.append("t.status = ?")
        args.append(status)
    if task_type:
        clauses.append("t.task_type = ?")
        args.append(task_type)
    if source_id:
        clauses.append(f"{_TASK_SOURCE_ID} = ?")
        args.append(source_id)
    if since is not None:
        clauses.append("t.created_at >= ?")
        args.append(since)
    if until is not None:
        clauses.append("t.created_at <= ?")
        args.append(until)
    if query:
        pattern = like_pattern(query)
        clauses.append(
            f"(v.title LIKE ? ESCAPE '{ESCAPE}' OR ms.name LIKE ? ESCAPE '{ESCAPE}'"
            f" OR cs.name LIKE ? ESCAPE '{ESCAPE}')"
        )
        args.extend([pattern] * 3)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, args


class SQLiteTaskRepository:
    """支持幂等创建、租约领取和恢复的任务仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, task: Task) -> Task:
        with self.database.transaction() as connection:
            insert_task(connection, task)
        return self.get_by_idempotency(task.idempotency_key) or task

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

    def latest_for_version(self, version_id: str, task_type: TaskType) -> Task | None:
        """用于消息列表分别显示翻译与内容加工的真实排队状态。"""

        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM tasks WHERE input_version_id=? AND task_type=?"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (version_id, task_type.value),
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
                  AND status = 'queued' AND available_at <= ?
                  AND (task_type != 'collect_source' OR NOT EXISTS (
                    SELECT 1 FROM tasks active WHERE active.status='running'
                    AND active.task_type='collect_source'
                    AND json_extract(active.payload_json, '$.source_id')=json_extract(tasks.payload_json, '$.source_id')
                  ))
                ORDER BY created_at
                LIMIT 1
                """,
                (*task_types, now),
            ).fetchone()
            if row is None:
                return None
            lease_until = now + lease_seconds
            lease_token = new_id()
            connection.execute(
                "UPDATE tasks SET status='running', attempts=attempts+1, lease_until=?, updated_at=?, error_code=NULL, error_message=NULL, lease_token=? WHERE id=?",
                (lease_until, now, lease_token, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (row["id"],)
            ).fetchone()
            return _task(claimed) if claimed else None

    def complete(
        self, task_id: str, output_version_id: str | None = None, lease_token: str | None = None
    ) -> Task:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET status='succeeded', lease_until=NULL, output_version_id=?, updated_at=?, lease_token=NULL WHERE id=? AND (? IS NULL OR lease_token=?)",
                (output_version_id, now(), task_id, lease_token, lease_token),
            )
        result = self.get(task_id)
        if result is None:
            raise KeyError(task_id)
        return result

    def fail(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        lease_token: str | None = None,
        retry_delay_seconds: int = 60,
    ) -> Task:
        status = TaskStatus.QUEUED.value if retryable else TaskStatus.FAILED.value
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET status=?, lease_until=NULL, error_code=?, error_message=?, updated_at=?, available_at=?, lease_token=NULL WHERE id=? AND (? IS NULL OR lease_token=?)",
                (
                    status,
                    error_code,
                    error_message[:1000],
                    now(),
                    now() + retry_delay_seconds if retryable else 0,
                    task_id,
                    lease_token,
                    lease_token,
                ),
            )
        result = self.get(task_id)
        if result is None:
            raise KeyError(task_id)
        return result

    def reclaim_expired(self, now: int) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='queued', lease_until=NULL, lease_token=NULL, updated_at=? WHERE status='running' AND lease_until < ?",
                (now, now),
            )
            return cursor.rowcount

    def list(
        self,
        status: str | None = None,
        task_type: str | None = None,
        source_id: str | None = None,
        query: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        """按筛选条件分页查询任务。"""
        where, args = _filters(status, task_type, source_id, query, since, until)
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"SELECT t.* {TASK_SOURCE} {where}"
                " ORDER BY t.created_at DESC, t.id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
            return [_task(row) for row in rows]
        finally:
            connection.close()

    def count(
        self,
        status: str | None = None,
        task_type: str | None = None,
        source_id: str | None = None,
        query: str | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> int:
        """与 list 同一套过滤条件与连接的总数，供列表分页使用。"""

        where, args = _filters(status, task_type, source_id, query, since, until)
        connection = self.database.connect()
        try:
            row = connection.execute(f"SELECT COUNT(*) {TASK_SOURCE} {where}", args).fetchone()
            return int(row[0])
        finally:
            connection.close()

    def retry(self, task_id: str) -> Task:
        """只重排失败任务，清零尝试次数。"""
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET status='queued', attempts=0, available_at=0, error_code=NULL, error_message=NULL, updated_at=? WHERE id=? AND status='failed'",
                (now(), task_id),
            )
        result = self.get(task_id)
        if result is None:
            raise KeyError(task_id)
        return result

    def delete_finished(self, task_ids: list[str]) -> int:
        """删除已结束的任务；状态条件写在 SQL 里，作为服务层校验之外的兜底。"""

        if not task_ids:
            return 0
        placeholders = ",".join("?" for _ in task_ids)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE id IN ("
                f"{placeholders}) AND status IN ('succeeded', 'failed')",
                tuple(task_ids),
            )
            return int(cursor.rowcount)

    def delete_failed(self) -> int:
        """清理全部失败任务。"""

        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE status = 'failed'")
            return int(cursor.rowcount)

    def renew(self, task_id: str, lease_token: str, now: int, lease_seconds: int) -> bool:
        """仅持有当前 token 的 worker 可以续租。"""
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET lease_until=?, updated_at=? WHERE id=? AND lease_token=? AND status='running'",
                (now + lease_seconds, now, task_id, lease_token),
            )
            return cursor.rowcount == 1
