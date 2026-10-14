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
from rss_v2.adapters.sqlite.keyword_aliases import displays
from rss_v2.adapters.sqlite.keyword_sql import (
    CURRENT_ENRICHMENT,
    LATEST_VERSION,
)
from rss_v2.domain import DomainError, PinCandidate, PinSuggestion


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


#: 候选统计的周数：把最近这些周按 7 天一段切开，看一个词跨了几段。
CANDIDATE_WEEKS = 4

#: 至少跨几周才算"周期性话题"。
CANDIDATE_MINIMUM_WEEKS = 3

#: 候选上限：模型每批只挑几条，候选太多反而稀释判断。
CANDIDATE_LIMIT = 60


def candidates(
    database: SQLiteDatabase,
    timestamp: int,
    since: int,
    weeks: int = CANDIDATE_WEEKS,
    minimum_weeks: int = CANDIDATE_MINIMUM_WEEKS,
) -> list[PinCandidate]:
    """跨周反复出现、且本周仍在窗口内的候选词。

    "周期性话题"是可算的：把最近 weeks 周按 7 天切段，统计一个词跨了几段；至少
    minimum_weeks 段、且最近一次出现落在热词窗口内（since）才算候选。已常驻的词除开。
    """

    connection = database.connect()
    try:
        rows = connection.execute(
            f"""
            SELECT keyword_key, COUNT(DISTINCT block) AS weeks,
                   COUNT(DISTINCT version_id) AS mentions
            FROM (
                SELECT COALESCE(a.canonical_norm, k.normalized) AS keyword_key,
                       CAST((? - COALESCE(v.published_at, v.collected_at)) / 604800 AS INTEGER)
                           AS block,
                       k.message_version_id AS version_id,
                       COALESCE(v.published_at, v.collected_at) AS seen_at
                FROM enrichment_keywords k
                JOIN message_versions v ON v.id = k.message_version_id
                JOIN messages m ON m.id = v.message_id
                LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
                WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
                  AND v.id = {LATEST_VERSION}
                  AND COALESCE(v.published_at, v.collected_at) >= ?
            )
            WHERE keyword_key NOT IN (SELECT key FROM keyword_pins)
            GROUP BY keyword_key
            HAVING COUNT(DISTINCT block) >= ? AND MAX(seen_at) >= ?
            ORDER BY mentions DESC, keyword_key
            LIMIT ?
            """,
            (timestamp, timestamp - weeks * 604800, minimum_weeks, since, CANDIDATE_LIMIT),
        ).fetchall()
        keys = [str(row["keyword_key"]) for row in rows]
        written = displays(database, keys)
        return [
            PinCandidate(
                key=key,
                text=written.get(key, (key, ""))[0],
                weeks=int(row["weeks"]),
            )
            for row, key in zip(rows, keys, strict=True)
        ]
    finally:
        connection.close()


def save_pin_suggestions(
    database: SQLiteDatabase, items: list[PinSuggestion], timestamp: int
) -> list[PinSuggestion]:
    """写入待确认的常驻建议；已有的键（含已忽略的）不覆盖。"""

    stored: list[PinSuggestion] = []
    with database.transaction() as connection:
        for item in items:
            # 逐条插入并看 rowcount：已有键（含已忽略的）不覆盖，也不该算作这次的建议数。
            cursor = connection.execute(
                """
                INSERT INTO keyword_pin_suggestions(key,text,rationale,status,weeks,
                    created_at,resolved_at)
                VALUES(?,?,?,'pending',?,?,NULL)
                ON CONFLICT(key) DO NOTHING
                """,
                (item.key, item.text, item.rationale, item.weeks, timestamp),
            )
            if cursor.rowcount:
                stored.append(item)
    return stored


def pin_suggestions(
    database: SQLiteDatabase, status: str | None, limit: int
) -> list[PinSuggestion]:
    """常驻建议列表；默认只看待处理的。"""

    connection = database.connect()
    try:
        if status:
            rows = connection.execute(
                "SELECT * FROM keyword_pin_suggestions WHERE status=?"
                " ORDER BY weeks DESC, created_at DESC, key LIMIT ?",
                (status, max(1, limit)),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM keyword_pin_suggestions"
                " ORDER BY weeks DESC, created_at DESC, key LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [_pin_suggestion(row) for row in rows]
    finally:
        connection.close()


def pending_pin_count(database: SQLiteDatabase) -> int:
    """待处理的常驻建议数。"""

    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM keyword_pin_suggestions WHERE status='pending'"
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        connection.close()


def resolve_pin_suggestion(database: SQLiteDatabase, key: str, status: str, timestamp: int) -> str:
    """处置一条常驻建议：采纳时顺带写入常驻表，返回展示写法。"""

    if status not in {"accepted", "dismissed"}:
        raise DomainError("keyword_pin_status_invalid", "处置只能是采纳或忽略")
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT text, status FROM keyword_pin_suggestions WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            raise DomainError("keyword_pin_suggestion_not_found", "常驻建议不存在")
        if str(row["status"]) != "pending":
            raise DomainError("keyword_pin_suggestion_resolved", "这条建议已经处理过了")
        if status == "accepted":
            connection.execute(
                "INSERT INTO keyword_pins(key,created_at) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                (key, timestamp),
            )
        connection.execute(
            "UPDATE keyword_pin_suggestions SET status=?, resolved_at=? WHERE key=?",
            (status, timestamp, key),
        )
        return str(row["text"])


def _pin_suggestion(row: sqlite3.Row) -> PinSuggestion:
    return PinSuggestion(
        key=str(row["key"]),
        text=str(row["text"]),
        rationale=str(row["rationale"]),
        weeks=int(row["weeks"]),
        status=str(row["status"]),
        created_at=int(row["created_at"]),
        resolved_at=row["resolved_at"],
    )
