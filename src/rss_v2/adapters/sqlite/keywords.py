"""关键词的存储、索引与检索。

共享的 SQL 片段与匹配模式在 keyword_sql.py，别名与合并在 keyword_aliases.py，
本模块只负责关键词索引本身与检索支撑。

`message_enrichments.keywords_json` 是一份快照（模型当时的原样输出），真正的检索面是
`enrichment_keywords` 关系表：每行一个匹配键。本模块是这张表唯一的读写入口，检索、
相关消息与索引重建都从这里走，避免同一段 SQL 在多处漂移。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from rss_v2.adapters.sqlite import keyword_overview, keyword_stats
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_aliases import history, merge, preview, resolve_key, undo
from rss_v2.adapters.sqlite.keyword_sql import (
    CURRENT_ENRICHMENT,
    KIND_WEIGHT,
    LATEST_VERSION,
    LATEST_VERSION_OF_MESSAGE,
    RESOLVED_KEY,
)
from rss_v2.domain import (
    Keyword,
    KeywordEntry,
    KeywordKind,
    KeywordOverview,
    MergePreview,
    MergeRecord,
    RelatedMessage,
)


def parse_keywords(value: str) -> tuple[Keyword, ...]:
    """读取关键词快照。

    结构化改造前后两种形状都要认：老行是字符串数组（按主题词处理），新行是
    `{"text","kind"}` 对象。异常内容按空处理，而不是让整个详情页读取失败。
    """

    try:
        parsed: object = json.loads(value)
    except ValueError:
        return ()
    if not isinstance(parsed, list):
        return ()
    # isinstance 已限定为列表，元素仍是任意 JSON 值，逐个折算成关键词。
    items = cast(list[object], parsed)
    result: list[Keyword] = []
    for item in items:
        if isinstance(item, dict):
            entry = cast(dict[str, object], item)
            text = str(entry.get("text") or "")
            kind_text = str(entry.get("kind") or KeywordKind.TOPIC.value)
        else:
            text = str(item)
            kind_text = KeywordKind.TOPIC.value
        if not text.strip():
            continue
        try:
            kind = KeywordKind(kind_text.strip().casefold())
        except ValueError:
            kind = KeywordKind.TOPIC
        result.append(Keyword(text=text, kind=kind))
    return tuple(result)


def encode_keywords(items: tuple[Keyword, ...]) -> str:
    """关键词列保存模型当时的输出形状，便于审计与事后重建索引。"""

    return json.dumps(
        [{"text": item.text, "kind": item.kind.value} for item in items], ensure_ascii=False
    )


def write_keywords(
    connection: sqlite3.Connection,
    enrichment_id: str,
    version_id: str,
    items: tuple[Keyword, ...],
) -> int:
    """把关键词写进关系表，返回写入行数。

    匹配键在这里统一计算，读取方不再重复归一化。同一匹配键只保留第一次出现（顺序即
    重要度次第）：升级前的结果里可能存在 "OpenAI" 与 "openai" 这类重复写法，重复入表
    会让频次与相关度重复计数。
    """

    connection.execute("DELETE FROM enrichment_keywords WHERE enrichment_id=?", (enrichment_id,))
    rows: list[tuple[str, str, int, str, str, str]] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.normalized
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append((enrichment_id, version_id, len(rows), item.text, normalized, item.kind.value))
    if not rows:
        return 0
    connection.executemany(
        """
        INSERT INTO enrichment_keywords(enrichment_id,message_version_id,ordinal,raw,normalized,kind)
        VALUES(?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


class SQLiteKeywordRepository:
    """关键词索引、相关消息与回填候选。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def rebuild_index(self) -> int:
        """按已存的关键词快照重建关系表，返回写入行数。

        结构化改造之前的结果只有 JSON、没有关系表行；本操作不调用模型，只把已有输出
        重新归一化入表，因此可以重复执行。
        """

        written = 0
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id, message_version_id, keywords_json FROM message_enrichments"
            ).fetchall()
            connection.execute("DELETE FROM enrichment_keywords")
            for row in rows:
                items = parse_keywords(row["keywords_json"])
                written += write_keywords(connection, row["id"], row["message_version_id"], items)
        return written

    def related_messages(self, message_id: str, limit: int = 8) -> list[RelatedMessage]:
        """与指定消息共享关键词的其他消息。

        只比较每条消息最新版本里"当前生效"的加工结果。没有向量，相关度就按共享词的
        类型计分：实体 4 分、主题 2 分、事件 1 分，同分再按发布时间新近。
        """

        connection = self.database.connect()
        try:
            base_rows = connection.execute(
                f"""
                SELECT {RESOLVED_KEY} AS keyword_key, k.raw, k.kind
                FROM enrichment_keywords k
                LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
                WHERE k.enrichment_id = (
                    SELECT e.id FROM message_enrichments e
                    WHERE e.message_version_id = {LATEST_VERSION_OF_MESSAGE}
                      AND e.status = 'succeeded'
                    ORDER BY e.updated_at DESC LIMIT 1)
                ORDER BY k.ordinal
                """,
                (message_id,),
            ).fetchall()
            # 同一规范词在一行里只算一次：合并后同一消息可能同时存在两种写法。
            base: dict[str, Keyword] = {}
            for row in base_rows:
                base.setdefault(
                    str(row["keyword_key"]),
                    Keyword(text=str(row["raw"]), kind=KeywordKind(str(row["kind"]))),
                )
            if not base:
                return []
            placeholders = ",".join("?" for _ in base)
            rows = connection.execute(
                f"""
                SELECT DISTINCT m.id AS message_id, m.source_id, v.id AS version_id,
                       v.title, v.published_at, v.collected_at,
                       {RESOLVED_KEY} AS keyword_key
                FROM enrichment_keywords k
                JOIN message_versions v ON v.id = k.message_version_id
                JOIN messages m ON m.id = v.message_id
                LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
                WHERE {RESOLVED_KEY} IN ({placeholders})
                  AND k.enrichment_id = {CURRENT_ENRICHMENT}
                  AND v.id = {LATEST_VERSION}
                  AND m.id <> ?
                """,
                (*base.keys(), message_id),
            ).fetchall()
        finally:
            connection.close()

        scores: dict[str, int] = {}
        shared: dict[str, dict[str, Keyword]] = {}
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            key = str(row["message_id"])
            keyword_key = str(row["keyword_key"])
            # 同一规范词的多写法只展示一次，避免"已合并的写法"在界面上重复出现。
            scored = shared.setdefault(key, {})
            if keyword_key in scored:
                continue
            # 展示用基准消息的写法：合并之后不该再把同一个概念显示成两个词。
            scored[keyword_key] = base[keyword_key]
            scores[key] = scores.get(key, 0) + KIND_WEIGHT.get(base[keyword_key].kind.value, 1)
            latest.setdefault(key, row)

        def rank(key: str) -> tuple[int, int]:
            row = latest[key]
            return (-scores[key], -int(row["published_at"] or row["collected_at"]))

        result: list[RelatedMessage] = []
        for key in sorted(scores, key=rank)[: max(1, limit)]:
            row = latest[key]
            result.append(
                RelatedMessage(
                    message_id=key,
                    source_id=str(row["source_id"]),
                    version_id=str(row["version_id"]),
                    title=str(row["title"]),
                    published_at=row["published_at"],
                    collected_at=int(row["collected_at"]),
                    shared=tuple(shared[key].values()),
                )
            )
        return result

    def vocabulary(
        self,
        query: str | None,
        kind: str | None,
        min_count: int,
        since: int | None,
        until: int | None,
        limit: int,
        offset: int,
    ) -> list[KeywordEntry]:
        """词表一页；统计口径见 keyword_stats。"""

        return keyword_stats.vocabulary(
            self.database, query, kind, min_count, since, until, limit, offset
        )

    def count_vocabulary(
        self,
        query: str | None,
        kind: str | None,
        min_count: int,
        since: int | None,
        until: int | None,
    ) -> int:
        """与词表同一条件的总数。"""

        return keyword_stats.count_vocabulary(self.database, query, kind, min_count, since, until)

    def overview(self, days: int, top_sources: int) -> KeywordOverview:
        """概览、类型构成、长尾、趋势、来源分布与覆盖。"""

        return keyword_overview.overview(self.database, days, top_sources)

    def resolve_key(self, key: str) -> str:
        """把输入键解析到规范词；不是别名时原样返回。"""

        return resolve_key(self.database, key)

    def merge_preview(self, sources: list[str], target: str) -> MergePreview:
        """合并影响面预览；与执行共用同一份计算。"""

        return preview(self.database, sources, target)

    def merge(self, sources: list[str], target: str) -> MergeRecord:
        """把若干写法并入目标规范词。"""

        return merge(self.database, sources, target)

    def undo_merge(self, merge_id: str) -> int:
        """撤销一次合并，返回恢复的行数。"""

        return undo(self.database, merge_id)

    def merges(self, limit: int = 20) -> list[MergeRecord]:
        """最近的合并记录，包含已撤销的那些。"""

        return history(self.database, limit)

    def messages_missing_enrichment(self, source_id: str | None, limit: int) -> list[str]:
        """没有成功加工结果的最新版本，按发布时间从新到旧。

        回填不套用自动入队的长度阈值：回填是人工触发、可预览、可限量的操作，目的是补齐
        存量；自动入队才需要靠阈值控制日常成本。
        """

        clauses = ["e.id IS NULL"]
        args: list[Any] = []
        if source_id:
            clauses.append("m.source_id = ?")
            args.append(source_id)
        connection = self.database.connect()
        try:
            rows = connection.execute(
                f"""
                SELECT m.id AS message_id
                FROM messages m
                JOIN message_versions v ON v.id = {LATEST_VERSION}
                LEFT JOIN message_enrichments e ON e.id = {CURRENT_ENRICHMENT}
                WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(v.published_at, v.collected_at) DESC, m.id
                LIMIT ?
                """,
                (*args, max(1, limit)),
            ).fetchall()
            return [str(row["message_id"]) for row in rows]
        finally:
            connection.close()
