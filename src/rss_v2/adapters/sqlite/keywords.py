"""关键词的存储、索引与检索。

`message_enrichments.keywords_json` 是一份快照（模型当时的原样输出），真正的检索面是
`enrichment_keywords` 关系表：每行一个匹配键。本模块是这张表唯一的读写入口，检索、
相关消息与索引重建都从这里走，避免同一段 SQL 在多处漂移。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.domain import Keyword, KeywordKind, RelatedMessage

#: LIKE 默认把 % 与 _ 当通配符；用户输入必须按字面匹配，因此统一转义并声明 ESCAPE。
ESCAPE = "\\"

#: 某个版本"当前生效"的加工结果：最近一次成功的那套。检索、相关消息与列表展示共用同一条
#: 定义，否则历史提示词版本产出的关键词会继续参与匹配，换提示词后的结果无法收敛。
CURRENT_ENRICHMENT = (
    "(SELECT e.id FROM message_enrichments e"
    " WHERE e.message_version_id = v.id AND e.status = 'succeeded'"
    " ORDER BY e.updated_at DESC LIMIT 1)"
)

#: 最新版本：列表、相关消息都只比较每条消息的最新版本。
LATEST_VERSION = (
    "(SELECT v2.id FROM message_versions v2"
    " WHERE v2.message_id = m.id ORDER BY v2.version_number DESC LIMIT 1)"
)

#: 指定消息 id 的最新版本，用于没有 messages 别名的子查询。
LATEST_VERSION_OF_MESSAGE = (
    "(SELECT v2.id FROM message_versions v2 WHERE v2.message_id = ?"
    " ORDER BY v2.version_number DESC LIMIT 1)"
)

#: 相关度计分：实体重合比主题与事件更能说明"说的是同一件事"。
KIND_WEIGHT = {"entity": 4, "topic": 2, "event": 1}


def escape_like(value: str) -> str:
    """反斜杠、百分号与下划线都按字面处理；调用方的 SQL 必须带 ESCAPE。"""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(value: str) -> str:
    """包含匹配的转义模式。"""

    return f"%{escape_like(value.strip())}%"


def prefix_pattern(value: str) -> str:
    """前缀匹配的转义模式。

    关键词用前缀而不是包含匹配：输入"尊界"要能命中"尊界v800"（同一主体的不同粒度），
    但"ai"不该命中"openai"——那正是旧实现里把 LIKE 打在 JSON 文本上的误命中。
    """

    return f"{escape_like(value)}%"


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
                SELECT k.raw, k.normalized, k.kind FROM enrichment_keywords k
                WHERE k.enrichment_id = (
                    SELECT e.id FROM message_enrichments e
                    WHERE e.message_version_id = {LATEST_VERSION_OF_MESSAGE}
                      AND e.status = 'succeeded'
                    ORDER BY e.updated_at DESC LIMIT 1)
                """,
                (message_id,),
            ).fetchall()
            base: dict[str, int] = {
                str(row["normalized"]): KIND_WEIGHT.get(str(row["kind"]), 1) for row in base_rows
            }
            if not base:
                return []
            placeholders = ",".join("?" for _ in base)
            rows = connection.execute(
                f"""
                SELECT m.id AS message_id, m.source_id, v.id AS version_id, v.title,
                       v.published_at, v.collected_at, k.raw, k.normalized, k.kind
                FROM enrichment_keywords k
                JOIN message_versions v ON v.id = k.message_version_id
                JOIN messages m ON m.id = v.message_id
                WHERE k.normalized IN ({placeholders})
                  AND k.enrichment_id = {CURRENT_ENRICHMENT}
                  AND v.id = {LATEST_VERSION}
                  AND m.id <> ?
                """,
                (*base.keys(), message_id),
            ).fetchall()
        finally:
            connection.close()

        scores: dict[str, int] = {}
        shared: dict[str, list[Keyword]] = {}
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            key = str(row["message_id"])
            scores[key] = scores.get(key, 0) + base.get(str(row["normalized"]), 1)
            shared.setdefault(key, []).append(
                Keyword(text=str(row["raw"]), kind=KeywordKind(str(row["kind"])))
            )
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
                    shared=tuple(shared[key]),
                )
            )
        return result

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
