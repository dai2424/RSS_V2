"""词表查询：按规范词键聚合出可翻阅的关键词清单。

口径与检索、相关消息一致：只算每条消息最新版本里"当前生效"的加工结果，时间用
`COALESCE(published_at, collected_at)`，词条按别名解析后的规范词键合并。
"""

from __future__ import annotations

import sqlite3

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_aliases import forms
from rss_v2.adapters.sqlite.keyword_sql import (
    ESCAPE,
    KEY_AGGREGATE,
    MAX_TIME,
    MIN_TIME,
    escape_like,
)
from rss_v2.domain import KeywordEntry, KeywordKind


def vocabulary(
    database: SQLiteDatabase,
    query: str | None,
    kind: str | None,
    min_count: int,
    since: int | None,
    until: int | None,
    limit: int,
    offset: int,
) -> list[KeywordEntry]:
    """词表一页：先聚合出规范词，再补展示写法与被归并进来的写法。"""

    where, args = _filters(query, kind, min_count, since, until)
    connection = database.connect()
    try:
        rows = connection.execute(
            f"""
            SELECT * FROM ({KEY_AGGREGATE})
            WHERE {where}
            ORDER BY mentions DESC, last_seen DESC, keyword_key
            LIMIT ? OFFSET ?
            """,
            (*args, max(1, limit), max(0, offset)),
        ).fetchall()
    finally:
        connection.close()
    writings: dict[str, list[tuple[str, str, int]]] = {}
    for key, raw, kind_value, versions in forms(
        database, [str(row["keyword_key"]) for row in rows]
    ):
        writings.setdefault(key, []).append((raw, kind_value, versions))
    return [_entry(row, writings.get(str(row["keyword_key"]), [])) for row in rows]


def count_vocabulary(
    database: SQLiteDatabase,
    query: str | None,
    kind: str | None,
    min_count: int,
    since: int | None,
    until: int | None,
) -> int:
    """与词表同一条件的总数，供分页显示。"""

    where, args = _filters(query, kind, min_count, since, until)
    connection = database.connect()
    try:
        row = connection.execute(
            f"SELECT COUNT(*) AS total FROM ({KEY_AGGREGATE}) WHERE {where}", tuple(args)
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        connection.close()


def _filters(
    query: str | None,
    kind: str | None,
    min_count: int,
    since: int | None,
    until: int | None,
) -> tuple[str, list[object]]:
    """词表的筛选条件；列表与计数必须走同一条，否则分页会对不上。"""

    clauses: list[str] = [f"keyword_key LIKE ? ESCAPE '{ESCAPE}'"] if query else []
    args: list[object] = [
        since if since is not None else MIN_TIME,
        until if until is not None else MAX_TIME,
    ]
    if query:
        args.append(f"%{escape_like(query.strip())}%")
    if kind:
        clauses.append("keyword_kind = ?")
        args.append(kind)
    clauses.append("mentions >= ?")
    args.append(max(1, min_count))
    return " AND ".join(clauses), args


def _entry(row: sqlite3.Row, writings: list[tuple[str, str, int]]) -> KeywordEntry:
    """把聚合行与写法列表拼成词表一行；展示写法用覆盖消息数最多的那种。"""

    ordered = sorted(writings, key=lambda item: (-item[2], item[0]))
    text = ordered[0][0] if ordered else str(row["keyword_key"])
    return KeywordEntry(
        key=str(row["keyword_key"]),
        text=text,
        kind=KeywordKind(str(row["keyword_kind"])),
        mentions=int(row["mentions"]),
        sources=int(row["sources"]),
        first_seen_at=int(row["first_seen"]),
        last_seen_at=int(row["last_seen"]),
        aliases=tuple(item[0] for item in ordered if item[0] != text),
    )
