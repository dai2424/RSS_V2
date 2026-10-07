"""collections SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from typing import Any

from rss_v2.adapters.sqlite.common import dumps, loads
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.tasks import insert_task
from rss_v2.domain import (
    CollectionRun,
    Task,
    TaskStatus,
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


class SQLiteCollectionRunRepository:
    """采集运行仓储。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, run: CollectionRun, tasks: list[Task]) -> CollectionRun:
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
            for task in tasks:
                insert_task(connection, task)
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

    def latest_for_source(self, source_id: str) -> CollectionRun | None:
        """从来源数组中查找最近的采集运行。"""
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM collection_runs WHERE EXISTS(SELECT 1 FROM json_each(source_ids_json, '$.source_ids') WHERE value=?) ORDER BY requested_at DESC, rowid DESC LIMIT 1",
                (source_id,),
            ).fetchone()
            return _run(row) if row else None
        finally:
            connection.close()

    def record_result(self, run_id: str, source_id: str, counts: dict[str, int]) -> None:
        """按来源 upsert 结果，汇总仅在所有来源完成后结束。"""
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO collection_results VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,source_id) DO UPDATE SET created_count=excluded.created_count,updated_count=excluded.updated_count,skipped_count=excluded.skipped_count,failed_count=excluded.failed_count",
                (
                    run_id,
                    source_id,
                    counts["created"],
                    counts["updated"],
                    counts["skipped"],
                    counts["failed"],
                ),
            )
            totals = connection.execute(
                "SELECT count(*) AS count,sum(created_count) AS created,sum(updated_count) AS updated,sum(skipped_count) AS skipped,sum(failed_count) AS failed FROM collection_results WHERE run_id=?",
                (run_id,),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM collection_runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None or totals is None:
                raise KeyError(run_id)
            # 结果写入时任务仍在执行，结束状态由 worker 处理重试后统一刷新。
            status = "running"
            connection.execute(
                "UPDATE collection_runs SET status=?, completed_at=?, created_count=?,updated_count=?,skipped_count=?,failed_count=? WHERE id=?",
                (
                    status,
                    None,
                    totals["created"],
                    totals["updated"],
                    totals["skipped"],
                    totals["failed"],
                    run_id,
                ),
            )

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
