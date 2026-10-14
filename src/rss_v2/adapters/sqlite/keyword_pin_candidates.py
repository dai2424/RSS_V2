"""常驻建议的候选：跨周反复出现的词。

候选由系统算出（可复算、可解释），模型只负责从中挑选——这是"常驻 = 周期话题"这条
语义的机械定义：把最近几周按 7 天切段，一个词至少跨几段、且最近一次出现落在热词窗口内。

单独成模块是为了让依赖单向：本模块依赖别名（取展示写法）与关键词聚合，而
`keyword_aliases` 在合并/撤销时需要 `keyword_pins`，两者不能再互相引用。
"""

from __future__ import annotations

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_aliases import displays
from rss_v2.adapters.sqlite.keyword_sql import CURRENT_ENRICHMENT, LATEST_VERSION
from rss_v2.domain import PinCandidate

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
