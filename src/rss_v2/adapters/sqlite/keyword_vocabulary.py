"""注入提示词的词表快照与词表版本。

口径与词表页、检索完全一致（共用 `keyword_sql.KEY_AGGREGATE`）：只算每条消息最新版本里
"当前生效"的加工结果，词条按别名解析后的规范词键合并。注入的是**展示写法**而不是匹配键，
模型能直接复用的才是写法。

三段内容：窗口内达到门槛的热词（按类型分组）、近期新增写法、常驻词。"近期新增"只列没进
前面分组的写法：同一个词在提示词里出现两次只会白费 token。

词表版本（`vocabulary_state`）记的是"模型看到的注入内容变没变"：指纹一致就不推进，
因此合并、新增词、改注入条数都会让它 +1，而重复加工同一条消息不会。
"""

from __future__ import annotations

import sqlite3

from rss_v2.adapters.sqlite import keyword_pins
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_aliases import displays
from rss_v2.adapters.sqlite.keyword_sql import (
    CURRENT_ENRICHMENT,
    KEY_AGGREGATE,
    LATEST_VERSION,
    MAX_TIME,
    RESOLVED_KEY,
)
from rss_v2.domain import KeywordKind, PromptVocabulary

#: "近期新增"一段的条数上限：它只是 Top N 之外的补充，不需要无限长。
RECENT_LIMIT = 30


def prompt_vocabulary(
    database: SQLiteDatabase,
    limit: int,
    recent_days: int,
    since: int,
    min_mentions: int,
    timestamp: int,
) -> PromptVocabulary:
    """取注入用词表：窗口内达到门槛的热词 + 近期新增写法 + 常驻词。

    热词段与词表页的热词视图同一口径（近 since 起、出现次数 ≥ min_mentions）：低于门槛的
    词不进词表页，也不该进提示词，否则"门槛"在注入与展示两处含义不同。常驻词不受窗口与
    门槛限制，永远出现。

    "近期新增"是**复用通道**，按首见时间而不是频次选取：新写法还来不及积累频次，而它恰恰
    是复用最该生效的时候；条数上限 30 与"不进热词段"的去重让它的 token 成本有界。

    limit <= 0 表示调用方关闭了注入。
    """

    if limit <= 0:
        return PromptVocabulary(entities=(), topics=(), events=(), recent=())
    size = max(1, limit)
    pinned = keyword_pins.pins(database)
    connection = database.connect()
    try:
        rows = connection.execute(
            f"""
            SELECT * FROM ({KEY_AGGREGATE})
            WHERE mentions >= ?
            ORDER BY mentions DESC, last_seen DESC, keyword_key
            LIMIT ?
            """,
            (since, MAX_TIME, max(1, min_mentions), size),
        ).fetchall()
        top_keys = [str(row["keyword_key"]) for row in rows]
        # 常驻词必然出现：超出条数上限也补进它所属的类型分组。
        extra_keys = [key for key in pinned if key not in set(top_keys)]
        recent_keys = _recent_keys(
            connection, recent_days, timestamp, exclude={*top_keys, *extra_keys}
        )
    finally:
        connection.close()
    written = displays(database, [*top_keys, *extra_keys, *recent_keys])
    groups: dict[str, list[str]] = {
        KeywordKind.ENTITY.value: [],
        KeywordKind.TOPIC.value: [],
        KeywordKind.EVENT.value: [],
    }
    for key in [*top_keys, *extra_keys]:
        text, kind_value = written.get(key, (key, KeywordKind.TOPIC.value))
        groups.setdefault(kind_value, []).append(text)
    return PromptVocabulary(
        entities=tuple(groups[KeywordKind.ENTITY.value]),
        topics=tuple(groups[KeywordKind.TOPIC.value]),
        events=tuple(groups[KeywordKind.EVENT.value]),
        recent=tuple(written.get(key, (key, ""))[0] for key in recent_keys),
    )


def note_vocabulary(database: SQLiteDatabase, fingerprint: str, timestamp: int) -> int:
    """记录本次注入的词表指纹，内容变化时推进版本号，返回当前版本。

    只在加工执行时调用：编译预览与提示词试跑只渲染、不记录，否则在界面上点一次预览
    就会让所有存量结果看起来"早于当前词表版本"。
    """

    with database.transaction() as connection:
        row = connection.execute(
            "SELECT fingerprint, revision FROM vocabulary_state WHERE id = 1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO vocabulary_state(id,fingerprint,revision,updated_at) VALUES(1,?,?,?)",
                (fingerprint, 1, timestamp),
            )
            return 1
        if str(row["fingerprint"]) == fingerprint:
            return int(row["revision"])
        revision = int(row["revision"]) + 1
        connection.execute(
            "UPDATE vocabulary_state SET fingerprint=?, revision=?, updated_at=? WHERE id = 1",
            (fingerprint, revision, timestamp),
        )
        return revision


def vocabulary_revision(connection: sqlite3.Connection) -> int:
    """当前词表版本；还没有任何加工记录时为 0。"""

    row = connection.execute("SELECT revision FROM vocabulary_state WHERE id = 1").fetchone()
    return int(row["revision"]) if row else 0


def _recent_keys(
    connection: sqlite3.Connection, recent_days: int, timestamp: int, exclude: set[str]
) -> list[str]:
    """近 N 天**首次出现**、且不在热词段里的规范词键，按首次出现从新到旧。

    刻意不套热词门槛：这里按首见时间选词，选的是"刚冒出来的写法"，而刚出现的写法不可能
    已有 5 次出现；用它做复用判定正是词表收敛的起点。
    """

    if recent_days <= 0:
        return []
    cutoff = timestamp - recent_days * 86400
    # 多取一段再剔除已在上面的词，避免 LIMIT 先截断导致"近期新增"整段消失。
    rows = connection.execute(
        f"""
        SELECT keyword_key, first_seen FROM (
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
        WHERE first_seen >= ?
        ORDER BY first_seen DESC, keyword_key
        LIMIT ?
        """,
        (cutoff, RECENT_LIMIT + len(exclude)),
    ).fetchall()
    return [key for row in rows if (key := str(row["keyword_key"])) not in exclude][:RECENT_LIMIT]
