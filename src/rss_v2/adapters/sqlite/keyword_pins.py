"""常驻词：人工标记"这个词必须一直可用"。

常驻只影响一件事——注入提示词时它必然出现，即使频次低、落在条数上限之外。它是词表
治理的人工锚点：周期性话题不必每次都被模型重新发明。

键是规范词键（与词表页的行键一致）。合并会把成员上的标记搬到目标词，撤销时按合并记录
还原，因此"pin 留在被合并掉的写法上"这种失效状态不会出现。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase


def set_pin(database: SQLiteDatabase, key: str, pinned: bool, timestamp: int) -> None:
    """标记或取消常驻；重复标记与取消不存在都视为成功（幂等）。"""

    with database.transaction() as connection:
        if pinned:
            connection.execute(
                "INSERT INTO keyword_pins(key,created_at) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                (key, timestamp),
            )
        else:
            connection.execute("DELETE FROM keyword_pins WHERE key=?", (key,))


def pins(database: SQLiteDatabase) -> tuple[str, ...]:
    """全部常驻词键，按标记时间从早到晚。"""

    connection = database.connect()
    try:
        return _keys(connection)
    finally:
        connection.close()


def _keys(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute("SELECT key FROM keyword_pins ORDER BY created_at, key").fetchall()
    return tuple(str(row["key"]) for row in rows)


def pinned_keys(connection: sqlite3.Connection) -> set[str]:
    """事务内读取常驻键集合；与 _keys 同一份数据，供列表与统计打标。"""

    return set(_keys(connection))


def move_to_target(
    connection: sqlite3.Connection, sources: list[str], target: str, timestamp: int
) -> dict[str, object]:
    """把成员上的常驻标记搬到目标词，返回要写进合并记录的两个事实。

    返回 `{"pinned": [...], "target_was_pinned": bool}`：成员里原本被标记的写法，
    以及目标词在合并前是否已经常驻（撤销时据此决定要不要清掉目标上的标记）。
    """

    moved = [key for key in sources if _exists(connection, key)]
    target_was_pinned = _exists(connection, target)
    if moved:
        if not target_was_pinned:
            connection.execute(
                "INSERT INTO keyword_pins(key,created_at) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                (target, timestamp),
            )
        placeholders = ",".join("?" for _ in moved)
        connection.execute(f"DELETE FROM keyword_pins WHERE key IN ({placeholders})", tuple(moved))
    return {"pinned": moved, "target_was_pinned": target_was_pinned}


def restore(
    connection: sqlite3.Connection,
    pinned_json: object,
    target: str,
    target_was_pinned: bool,
    timestamp: int,
) -> None:
    """撤销合并时还原常驻标记：还给原写法，并清掉本次合并才戴上的目标标记。"""

    keys = _parse(pinned_json)
    for key in keys:
        connection.execute(
            "INSERT INTO keyword_pins(key,created_at) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
            (key, timestamp),
        )
    if keys and not target_was_pinned:
        connection.execute("DELETE FROM keyword_pins WHERE key=?", (target,))


def _exists(connection: sqlite3.Connection, key: str) -> bool:
    return (
        connection.execute("SELECT 1 FROM keyword_pins WHERE key=?", (key,)).fetchone() is not None
    )


def _parse(pinned_json: object) -> list[str]:
    """合并记录里的成员键列表；异常内容按空处理，不让撤销整体失败。"""

    if not isinstance(pinned_json, str):
        return []
    try:
        raw: object = json.loads(pinned_json)
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in cast(list[Any], raw) if str(item)]
