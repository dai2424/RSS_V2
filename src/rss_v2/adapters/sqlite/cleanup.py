"""删除类操作的共用清理件。

删除来源与删除消息都要按依赖顺序显式清理没有外键的表（任务、导入台账），
这里集中一份，避免两处各写一套顺序。
"""

from __future__ import annotations

from typing import Any


def ids(connection: Any, sql: str, args: tuple[Any, ...]) -> list[str]:
    """按 SQL 取一组 id。"""

    return [str(row[0]) for row in connection.execute(sql, args).fetchall()]


def delete_values(connection: Any, table: str, column: str, values: list[str]) -> None:
    """按 id 列表删除；空列表直接返回。"""

    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", values)


def delete_import_map(connection: Any, entity_kind: str, target_ids: list[str]) -> None:
    """清理 v1 导入台账中指向已删除实体的映射，让这些记录可以重新导入。"""

    if not target_ids:
        return
    placeholders = ",".join("?" for _ in target_ids)
    connection.execute(
        f"DELETE FROM v1_import_map WHERE entity_kind = ? AND target_id IN ({placeholders})",
        (entity_kind, *target_ids),
    )
