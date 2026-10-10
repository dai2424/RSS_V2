"""消息删除的级联清理与影响面计算，集中在一个事务内完成。

删除范围：消息本体（版本 → 译文 / 加工结果 → 关键词随外键级联）、这些版本的翻译与
加工任务、以及 v1 导入台账里指向它们的映射（不清台账，v1 再导入会被跳过）。

两件刻意不做的事：
- **保留 `llm_calls`**：那是模型调用审计，也是"提示词是否被使用"的依据；来源删除会连带
  清理它，是因为整个来源都没了，单条消息删除保留即可。
- **不动 `collection_results`**：采集运行的历史快照属于来源维度。

删除后同一 feed 条目会在下次采集重新建消息（采集按 external_id 去重，没有墓石标记），
界面上必须把这一点告诉用户。
"""

from __future__ import annotations

from typing import Any

from rss_v2.adapters.sqlite.cleanup import delete_import_map, delete_values, ids
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import MessageDeletion


def impact(database: SQLiteDatabase, message_ids: list[str]) -> MessageDeletion:
    """计算删除影响面，不改动任何数据。"""

    connection = database.connect()
    try:
        return _summary(connection, message_ids)
    finally:
        connection.close()


def delete_messages(database: SQLiteDatabase, message_ids: list[str]) -> MessageDeletion:
    """删除消息及其从属数据，返回实际删掉的数量。"""

    with database.transaction() as connection:
        summary = _summary(connection, message_ids)
        version_ids = _version_ids(connection, message_ids)
        task_ids = _task_ids(connection, version_ids)
        # tasks.input_version_id 没有外键，删消息不会连带任务，必须显式按版本清理。
        delete_values(connection, "tasks", "id", task_ids)
        delete_import_map(connection, "message", message_ids)
        delete_import_map(connection, "version", version_ids)
        delete_values(connection, "messages", "id", message_ids)
    return summary


def _version_ids(connection: Any, message_ids: list[str]) -> list[str]:
    if not message_ids:
        return []
    placeholders = ",".join("?" for _ in message_ids)
    return ids(
        connection,
        f"SELECT id FROM message_versions WHERE message_id IN ({placeholders})",
        tuple(message_ids),
    )


def _task_ids(connection: Any, version_ids: list[str]) -> list[str]:
    if not version_ids:
        return []
    placeholders = ",".join("?" for _ in version_ids)
    return ids(
        connection,
        "SELECT id FROM tasks WHERE task_type IN ('translate_message', 'enrich_message')"
        f" AND input_version_id IN ({placeholders})",
        tuple(version_ids),
    )


def _summary(connection: Any, message_ids: list[str]) -> MessageDeletion:
    """影响面：只统计真正存在的消息及其从属数据。"""

    if not message_ids:
        return MessageDeletion(0, 0, 0, 0, 0)
    placeholders = ",".join("?" for _ in message_ids)
    args = tuple(message_ids)
    messages = int(
        connection.execute(
            f"SELECT COUNT(*) FROM messages WHERE id IN ({placeholders})", args
        ).fetchone()[0]
    )
    versions = int(
        connection.execute(
            f"SELECT COUNT(*) FROM message_versions WHERE message_id IN ({placeholders})", args
        ).fetchone()[0]
    )
    versions_subquery = f"SELECT id FROM message_versions WHERE message_id IN ({placeholders})"
    translations = int(
        connection.execute(
            f"SELECT COUNT(*) FROM translations WHERE message_version_id IN ({versions_subquery})",
            args,
        ).fetchone()[0]
    )
    enrichments = int(
        connection.execute(
            "SELECT COUNT(*) FROM message_enrichments"
            f" WHERE message_version_id IN ({versions_subquery})",
            args,
        ).fetchone()[0]
    )
    tasks = int(
        connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_type IN ('translate_message', 'enrich_message')"
            f" AND input_version_id IN ({versions_subquery})",
            args,
        ).fetchone()[0]
    )
    return MessageDeletion(
        messages=messages,
        versions=versions,
        translations=translations,
        enrichments=enrichments,
        tasks=tasks,
    )
