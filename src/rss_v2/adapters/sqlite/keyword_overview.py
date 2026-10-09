"""关键词概览：规模、类型构成、长尾、趋势、来源分布与覆盖情况。

所有数字都从库里算出来，不做估算也不填演示值——页面上显示什么，就必须能用同一套口径复算。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_sql import (
    CURRENT_ENRICHMENT,
    KEY_AGGREGATE,
    LATEST_VERSION,
    MAX_TIME,
    MIN_TIME,
    RESOLVED_KEY,
)
from rss_v2.domain import (
    Keyword,
    KeywordBucket,
    KeywordKind,
    KeywordKindStat,
    KeywordOverview,
    KeywordSourceStat,
    KeywordTrendPoint,
)
from rss_v2.domain.values import now

#: 长尾档位：孤词最多，后面按 2、3、4-5、6 以上归并；文案在这里统一维护。
_LONG_TAIL_LABELS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "只出现一次"),
    (2, 2, "出现 2 次"),
    (3, 3, "出现 3 次"),
    (4, 5, "出现 4-5 次"),
    (6, 0, "出现 6 次以上"),
)


def overview(database: SQLiteDatabase, days: int, top_sources: int) -> KeywordOverview:
    """概览：把页面上要用的数字一次性算好，避免多次往返与口径漂移。"""

    connection = database.connect()
    try:
        messages, enriched = _coverage(connection)
        keys = connection.execute(
            f"SELECT * FROM ({KEY_AGGREGATE})", (MIN_TIME, MAX_TIME)
        ).fetchall()
        aliases = int(connection.execute("SELECT COUNT(*) FROM keyword_aliases").fetchone()[0])
        merges = int(
            connection.execute(
                "SELECT COUNT(*) FROM keyword_merges WHERE undone_at IS NULL"
            ).fetchone()[0]
        )
        tasks = {
            str(row["status"]): int(row["total"])
            for row in connection.execute(
                "SELECT status, COUNT(*) AS total FROM tasks"
                " WHERE task_type='enrich_message' GROUP BY status"
            )
        }
        trend = _trend(connection, days)
        sources = _sources(connection, top_sources)
    finally:
        connection.close()

    mentions = sum(int(row["mentions"]) for row in keys)
    return KeywordOverview(
        messages=messages,
        enriched=enriched,
        pending=messages - enriched,
        terms=len(keys),
        mentions=mentions,
        singletons=sum(1 for row in keys if int(row["mentions"]) == 1),
        average_per_message=round(mentions / enriched, 2) if enriched else 0.0,
        aliases=aliases,
        merges=merges,
        kinds=_kinds(keys),
        long_tail=_long_tail(keys),
        trend=trend,
        sources=sources,
        tasks_queued=tasks.get("queued", 0),
        tasks_running=tasks.get("running", 0),
        tasks_succeeded=tasks.get("succeeded", 0),
        tasks_failed=tasks.get("failed", 0),
    )


def _coverage(connection: sqlite3.Connection) -> tuple[int, int]:
    """消息总数与其中有生效加工结果的条数。"""

    row = connection.execute(
        f"""
        SELECT COUNT(*) AS messages,
               SUM(CASE WHEN e.id IS NOT NULL THEN 1 ELSE 0 END) AS enriched
        FROM messages m
        JOIN message_versions v ON v.id = {LATEST_VERSION}
        LEFT JOIN message_enrichments e ON e.id = {CURRENT_ENRICHMENT}
        """
    ).fetchone()
    return int(row["messages"] or 0), int(row["enriched"] or 0)


def _kinds(keys: list[sqlite3.Row]) -> tuple[KeywordKindStat, ...]:
    counts: dict[str, tuple[int, int]] = {}
    for row in keys:
        kind = str(row["keyword_kind"])
        terms, mentions = counts.get(kind, (0, 0))
        counts[kind] = (terms + 1, mentions + int(row["mentions"]))
    return tuple(
        KeywordKindStat(kind=KeywordKind(kind), terms=terms, mentions=mentions)
        for kind, (terms, mentions) in sorted(counts.items())
    )


def _long_tail(keys: list[sqlite3.Row]) -> tuple[KeywordBucket, ...]:
    buckets = [0] * len(_LONG_TAIL_LABELS)
    for row in keys:
        mentions = int(row["mentions"])
        for index, (low, high, _) in enumerate(_LONG_TAIL_LABELS):
            if mentions >= low and (high == 0 or mentions <= high):
                buckets[index] += 1
                break
    return tuple(
        KeywordBucket(label=label, terms=buckets[index])
        for index, (_, _, label) in enumerate(_LONG_TAIL_LABELS)
    )


def _trend(connection: sqlite3.Connection, days: int) -> tuple[KeywordTrendPoint, ...]:
    """近 N 天（含今天）的每日词位数与当天首次出现的规范词数，没有数据的天补 0。"""

    window = max(1, days)
    today = datetime.fromtimestamp(now(), tz=UTC).date()
    start = today - timedelta(days=window - 1)
    start_ts = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp())
    dates = [(start + timedelta(days=offset)).isoformat() for offset in range(window)]
    produced = {
        str(row["day"]): int(row["mentions"])
        for row in connection.execute(
            f"""
            SELECT date(COALESCE(v.published_at, v.collected_at), 'unixepoch') AS day,
                   COUNT(*) AS mentions
            FROM enrichment_keywords k
            JOIN message_versions v ON v.id = k.message_version_id
            JOIN messages m ON m.id = v.message_id
            LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
            WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
              AND v.id = {LATEST_VERSION}
              AND COALESCE(v.published_at, v.collected_at) >= ?
            GROUP BY day
            """,
            (start_ts,),
        )
    }
    fresh = {
        str(row["day"]): int(row["total"])
        for row in connection.execute(
            f"""
            SELECT date(first_seen, 'unixepoch') AS day, COUNT(*) AS total
            FROM (
                SELECT {RESOLVED_KEY} AS keyword_key,
                       MIN(COALESCE(v.published_at, v.collected_at)) AS first_seen
                FROM enrichment_keywords k
                JOIN message_versions v ON v.id = k.message_version_id
                JOIN messages m ON m.id = v.message_id
                LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
                WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
                  AND v.id = {LATEST_VERSION}
                GROUP BY keyword_key
            )
            GROUP BY day
            """
        )
    }
    return tuple(
        KeywordTrendPoint(day=day, mentions=produced.get(day, 0), new_terms=fresh.get(day, 0))
        for day in dates
    )


def _sources(connection: sqlite3.Connection, limit: int) -> tuple[KeywordSourceStat, ...]:
    """来源分布：按词位数排序的 Top N 来源，附带该来源出现最多的三个写法。"""

    rows = connection.execute(
        f"""
        SELECT m.source_id AS source_id, s.name AS source_name,
               COUNT(DISTINCT {RESOLVED_KEY}) AS terms, COUNT(*) AS mentions
        FROM enrichment_keywords k
        JOIN message_versions v ON v.id = k.message_version_id
        JOIN messages m ON m.id = v.message_id
        JOIN rss_sources s ON s.id = m.source_id
        LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
        WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
          AND v.id = {LATEST_VERSION}
        GROUP BY m.source_id, s.name
        ORDER BY mentions DESC, source_name
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    if not rows:
        return ()
    source_ids = [str(row["source_id"]) for row in rows]
    placeholders = ",".join("?" for _ in source_ids)
    writing_rows = connection.execute(
        f"""
        SELECT m.source_id AS source_id, k.raw AS raw, k.kind AS kind, COUNT(*) AS versions
        FROM enrichment_keywords k
        JOIN message_versions v ON v.id = k.message_version_id
        JOIN messages m ON m.id = v.message_id
        WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
          AND v.id = {LATEST_VERSION}
          AND m.source_id IN ({placeholders})
        GROUP BY m.source_id, k.raw, k.kind
        ORDER BY versions DESC, raw
        """,
        tuple(source_ids),
    ).fetchall()
    grouped: dict[str, list[Keyword]] = {}
    for row in writing_rows:
        bucket = grouped.setdefault(str(row["source_id"]), [])
        if len(bucket) < 3:
            bucket.append(Keyword(text=str(row["raw"]), kind=KeywordKind(str(row["kind"]))))
    return tuple(
        KeywordSourceStat(
            source_id=str(row["source_id"]),
            source_name=str(row["source_name"]),
            terms=int(row["terms"]),
            mentions=int(row["mentions"]),
            top=tuple(grouped.get(str(row["source_id"]), [])),
        )
        for row in rows
    )
