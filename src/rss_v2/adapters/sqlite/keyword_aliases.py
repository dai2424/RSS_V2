"""关键词别名、人工合并与合并历史。

合并只处理"同指"：`OpenAI` / `openai` / `Open AI` 是同一个东西的不同写法，合并后检索
与统计都按规范词键说话。**不处理上下位**（`尊界` 与 `尊界V800`），那会抹平检索粒度。

别名一律压平到最终规范词，所以解析只有一跳：不需要递归 CTE，统计里一次 LEFT JOIN 即可。
撤销按行记录的原值回填，因此链式合并（A→B 之后把 B 并入 C）也能逐次解开而不产生漂移。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.keyword_sql import (
    CURRENT_ENRICHMENT,
    LATEST_VERSION,
    RESOLVED_KEY,
)
from rss_v2.domain import (
    DomainError,
    Keyword,
    KeywordKind,
    MergePreview,
    MergeRecord,
)
from rss_v2.domain.values import new_id, now


def resolve_key(database: SQLiteDatabase, key: str) -> str:
    """把输入键解析到规范词；不是别名时原样返回。"""

    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT canonical_norm FROM keyword_aliases WHERE alias_norm=?", (key,)
        ).fetchone()
        return str(row["canonical_norm"]) if row else key
    finally:
        connection.close()


def _forms(connection: sqlite3.Connection, keys: list[str]) -> list[tuple[str, str, str, int]]:
    """取这些规范词键下的全部写法： (规范词键, raw, kind, 覆盖消息数)。

    只统计每条消息最新版本里"当前生效"的加工结果，与词表、检索保持同一口径。
    """

    placeholders = ",".join("?" for _ in keys)
    rows = connection.execute(
        f"""
        SELECT {RESOLVED_KEY} AS keyword_key, k.raw AS raw, k.kind AS kind,
               COUNT(DISTINCT k.message_version_id) AS versions
        FROM enrichment_keywords k
        JOIN message_versions v ON v.id = k.message_version_id
        JOIN messages m ON m.id = v.message_id
        LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
        WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
          AND v.id = {LATEST_VERSION}
          AND {RESOLVED_KEY} IN ({placeholders})
        GROUP BY keyword_key, k.raw, k.kind
        ORDER BY versions DESC, raw
        """,
        tuple(keys),
    ).fetchall()
    return [
        (str(row["keyword_key"]), str(row["raw"]), str(row["kind"]), int(row["versions"]))
        for row in rows
    ]


def _affected_messages(connection: sqlite3.Connection, keys: list[str]) -> int:
    """这些规范词键覆盖的消息数（按消息版本去重）。"""

    placeholders = ",".join("?" for _ in keys)
    row = connection.execute(
        f"""
        SELECT COUNT(DISTINCT k.message_version_id) AS total
        FROM enrichment_keywords k
        JOIN message_versions v ON v.id = k.message_version_id
        JOIN messages m ON m.id = v.message_id
        LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized
        WHERE k.enrichment_id = {CURRENT_ENRICHMENT}
          AND v.id = {LATEST_VERSION}
          AND {RESOLVED_KEY} IN ({placeholders})
        """,
        tuple(keys),
    ).fetchone()
    return int(row["total"]) if row else 0


def _pick_display(forms: list[tuple[str, str, str, int]]) -> tuple[str, str]:
    """词条的展示写法与类型：取覆盖消息数最多的写法，类型按实体优先裁决。"""

    if not forms:
        return "", KeywordKind.TOPIC.value
    order = {KeywordKind.ENTITY.value: 0, KeywordKind.TOPIC.value: 1, KeywordKind.EVENT.value: 2}
    ordered = sorted(forms, key=lambda item: (-item[3], order.get(item[2], 3), item[1]))
    return ordered[0][1], ordered[0][2]


def preview(database: SQLiteDatabase, sources: list[str], target: str) -> MergePreview:
    """合并影响面：预览与执行共用这份计算，避免两处口径不一致。"""

    connection = database.connect()
    try:
        keys = sorted({*sources, target})
        rows = _forms(connection, keys)
        forms = [Keyword(text=raw, kind=KeywordKind(kind)) for _, raw, kind, _ in rows]
        mentions = sum(count for *_, count in rows)
        messages = _affected_messages(connection, keys)
    finally:
        connection.close()
    return MergePreview(
        sources=tuple(sorted(sources)),
        target=target,
        forms=tuple(forms),
        mentions=mentions,
        messages=messages,
    )


def _target_row(connection: sqlite3.Connection, target: str) -> tuple[str, str]:
    """目标词的展示写法；目标必须已经在词表里，不存在就明确失败。"""

    rows = _forms(connection, [target])
    if not rows:
        raise DomainError("keyword_target_unknown", "目标词在词表里不存在，先确认它已经有加工结果")
    return _pick_display(rows)


def merge(database: SQLiteDatabase, sources: list[str], target: str) -> MergeRecord:
    """把 sources 这些规范词并入 target，返回合并记录。"""

    if not sources:
        raise DomainError("keyword_merge_empty", "至少选择一个要并入的写法")
    if target in sources:
        raise DomainError("keyword_merge_conflict", "目标词不能同时是要被并入的词")
    merge_id = new_id()
    timestamp = now()
    previewed = preview(database, sources, target)
    with database.transaction() as connection:
        target_raw, _ = _target_row(connection, target)
        for source in sources:
            # 先把已经是该词别名的行改指目标，并记住原值以便撤销。
            connection.execute(
                """
                UPDATE keyword_aliases
                SET canonical_norm=?, merge_id=?, previous_canonical=canonical_norm,
                    previous_merge_id=merge_id
                WHERE canonical_norm=?
                """,
                (target, merge_id, source),
            )
            # 源词本身作为写法也要指过去；已有别名行（例如它曾被并入别处）不覆盖。
            connection.execute(
                """
                INSERT INTO keyword_aliases(alias_norm,canonical_norm,merge_id,created_at)
                VALUES(?,?,?,?)
                ON CONFLICT(alias_norm) DO NOTHING
                """,
                (source, target, merge_id, timestamp),
            )
        connection.execute(
            """
            INSERT INTO keyword_merges(id,target_norm,target_raw,members_json,affected_messages,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                merge_id,
                target,
                target_raw,
                json.dumps(
                    [{"text": item.text, "kind": item.kind.value} for item in previewed.forms],
                    ensure_ascii=False,
                ),
                previewed.messages,
                timestamp,
            ),
        )
    return MergeRecord(
        id=merge_id,
        target_key=target,
        target_raw=target_raw,
        members=previewed.forms,
        messages=previewed.messages,
        created_at=timestamp,
        undone_at=None,
    )


def undo(database: SQLiteDatabase, merge_id: str) -> int:
    """撤销一次合并，返回恢复的行数。

    由本次合并新建的别名直接删除；被改写的别名按 `previous_canonical` 回填，
    因此链式合并可以逐次解开，而不是一次性抹掉所有指向。
    """

    with database.transaction() as connection:
        row = connection.execute(
            "SELECT undone_at FROM keyword_merges WHERE id=?", (merge_id,)
        ).fetchone()
        if row is None:
            raise DomainError("keyword_merge_not_found", "合并记录不存在")
        if row["undone_at"] is not None:
            raise DomainError("keyword_merge_undone", "这次合并已经撤销过了")
        created = connection.execute(
            "SELECT COUNT(*) FROM keyword_aliases WHERE merge_id=? AND previous_canonical IS NULL",
            (merge_id,),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM keyword_aliases WHERE merge_id=? AND previous_canonical IS NULL",
            (merge_id,),
        )
        restored = connection.execute(
            """
            UPDATE keyword_aliases
            SET canonical_norm=previous_canonical, merge_id=previous_merge_id,
                previous_canonical=NULL, previous_merge_id=NULL
            WHERE merge_id=? AND previous_canonical IS NOT NULL
            """,
            (merge_id,),
        ).rowcount
        connection.execute("UPDATE keyword_merges SET undone_at=? WHERE id=?", (now(), merge_id))
    return int(created) + int(restored)


def history(database: SQLiteDatabase, limit: int = 20) -> list[MergeRecord]:
    """最近的合并记录，包含已撤销的那些。"""

    connection = database.connect()
    try:
        rows = connection.execute(
            "SELECT * FROM keyword_merges ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    finally:
        connection.close()
    return [_record(row) for row in rows]


def _record(row: sqlite3.Row) -> MergeRecord:
    raw: object = json.loads(row["members_json"])
    members: list[Keyword] = []
    for item in cast(list[Any], raw) if isinstance(raw, list) else []:
        entry = cast(dict[str, Any], item) if isinstance(item, dict) else {}
        text = str(entry.get("text") or "")
        if text:
            members.append(Keyword(text=text, kind=KeywordKind(str(entry.get("kind") or "topic"))))
    return MergeRecord(
        id=str(row["id"]),
        target_key=str(row["target_norm"]),
        target_raw=str(row["target_raw"]),
        members=tuple(members),
        messages=int(row["affected_messages"]),
        created_at=int(row["created_at"]),
        undone_at=row["undone_at"],
    )
