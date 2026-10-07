"""来源删除的级联清理，集中在一个事务内完成。

删除范围：来源本体（健康记录随外键级联）、消息（版本与译文随外键级联）、
采集结果、相关任务与任务审计、v1 导入映射。未完成的采集运行移除该来源，
由服务层随后重算状态，避免留下永远运行中的运行记录。
"""

from __future__ import annotations

import json
from typing import Any

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import SourceDeletion

# 未完成运行：状态仍是排队或运行中，需要收敛
_UNFINISHED = ("queued", "running")


def _ids(connection: Any, sql: str, args: tuple[Any, ...]) -> list[str]:
    return [str(row[0]) for row in connection.execute(sql, args).fetchall()]


def _delete_values(connection: Any, table: str, column: str, values: list[str]) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values)


def _delete_import_map(connection: Any, entity_kind: str, target_ids: list[str]) -> None:
    """清理 v1 导入台账中指向已删除实体的映射。"""

    if not target_ids:
        return
    placeholders = ",".join("?" for _ in target_ids)
    connection.execute(
        f"DELETE FROM v1_import_map WHERE entity_kind = ? AND target_id IN ({placeholders})",
        (entity_kind, *target_ids),
    )


def delete_source(database: SQLiteDatabase, source_id: str) -> SourceDeletion:
    """删除来源及其全部从属数据；来源不存在时抛 KeyError。

    外键开启且 messages/tasks 等对来源无级联，因此必须按依赖顺序显式清理。
    """

    with database.transaction() as connection:
        row = connection.execute("SELECT id FROM rss_sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)

        message_ids = _ids(connection, "SELECT id FROM messages WHERE source_id = ?", (source_id,))
        version_ids = _ids(
            connection,
            "SELECT id FROM message_versions WHERE message_id IN "
            "(SELECT id FROM messages WHERE source_id = ?)",
            (source_id,),
        )
        health_ids = _ids(
            connection,
            "SELECT id FROM source_health_checks WHERE source_id = ?",
            (source_id,),
        )
        task_ids = _ids(
            connection,
            "SELECT id FROM tasks WHERE task_type = 'collect_source' "
            "AND json_extract(payload_json, '$.source_id') = ?",
            (source_id,),
        )
        if version_ids:
            placeholders = ",".join("?" for _ in version_ids)
            task_ids += _ids(
                connection,
                "SELECT id FROM tasks WHERE task_type = 'translate_message' "
                f"AND input_version_id IN ({placeholders})",
                tuple(version_ids),
            )

        unfinished_runs = _remove_source_from_unfinished_runs(connection, source_id)

        # 任务审计与任务本体；llm_calls.task_id 无外键，需显式清理
        _delete_values(connection, "llm_calls", "task_id", task_ids)
        _delete_values(connection, "tasks", "id", task_ids)

        # 导入台账：清理后同一 v1 记录可以重新导入
        connection.execute(
            "DELETE FROM v1_import_map WHERE entity_kind = 'source' AND target_id = ?",
            (source_id,),
        )
        _delete_import_map(connection, "message", message_ids)
        _delete_import_map(connection, "version", version_ids)
        _delete_import_map(connection, "health", health_ids)

        connection.execute("DELETE FROM collection_results WHERE source_id = ?", (source_id,))
        connection.execute("DELETE FROM messages WHERE source_id = ?", (source_id,))
        cursor = connection.execute("DELETE FROM rss_sources WHERE id = ?", (source_id,))
        if cursor.rowcount == 0:
            raise KeyError(source_id)
    return SourceDeletion(messages=len(message_ids), unfinished_run_ids=tuple(unfinished_runs))


def _remove_source_from_unfinished_runs(connection: Any, source_id: str) -> list[str]:
    """把来源从未完成运行的来源列表移除，返回这些运行 ID 供服务层重算状态。"""

    rows = connection.execute(
        "SELECT id, source_ids_json FROM collection_runs WHERE status IN (?, ?) "
        "AND EXISTS(SELECT 1 FROM json_each(source_ids_json, '$.source_ids') WHERE value = ?)",
        (*_UNFINISHED, source_id),
    ).fetchall()
    run_ids: list[str] = []
    for row in rows:
        payload = json.loads(row["source_ids_json"])
        remaining = [item for item in payload.get("source_ids", []) if item != source_id]
        payload["source_ids"] = remaining
        connection.execute(
            "UPDATE collection_runs SET source_ids_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
        run_ids.append(str(row["id"]))
    return run_ids
