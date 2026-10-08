"""v1 消息与历史版本的无损导入。"""

from __future__ import annotations

import hashlib
import sqlite3

from rss_v2.adapters.sqlite.v1_common import legacy_uuid, mapped, record, remember
from rss_v2.domain.values import detect_language, message_content_hash, normalize_url


def _values(
    message: sqlite3.Row, revision: sqlite3.Row | None
) -> tuple[str, str, str, str, int | None, int]:
    """保留版本原文；旧版本缺少链接和采集时间时用消息链接、观察时间兜底。"""
    row = revision if revision is not None else message
    url = str(
        (revision["source_url"] if revision is not None else message["url"]) or message["url"] or ""
    )
    return (
        str(row["title"] or ""),
        str(row["summary"] or ""),
        str(row["content"] or ""),
        normalize_url(url) if url else "",
        row["published_ts"],
        int(row["collected_ts"] or (revision["observed_ts"] if revision is not None else 0)),
    )


def _version(
    target: sqlite3.Connection,
    message_id: str,
    legacy_id: str,
    values: tuple[str, str, str, str, int | None, int],
    language: str | None,
    counts: dict[str, int],
) -> tuple[str, bool]:
    """追加未映射版本，按 v2 字段顺序重算内容哈希，不覆盖历史快照。"""
    existing = mapped(target, "version", legacy_id)
    record(counts, "versions", existing is None)
    if existing:
        return existing, False
    title, summary, content, url, published, collected = values
    # 与采集器同一口径：正文只是摘要的副本时留空，避免详情页重复展示。
    content = "" if content == summary else content
    version_id = legacy_uuid("version", legacy_id)
    number = target.execute(
        "SELECT coalesce(max(version_number),0)+1 FROM message_versions WHERE message_id=?",
        (message_id,),
    ).fetchone()[0]
    target.execute(
        "INSERT INTO message_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            version_id,
            message_id,
            number,
            title,
            summary,
            content,
            url,
            published,
            collected,
            language
            if language in {"en", "zh", "mixed"}
            else detect_language(f"{title} {summary} {content}").value,
            message_content_hash(title, summary, content),
        ),
    )
    remember(target, "version", legacy_id, version_id)
    return version_id, True


def _order_versions(target: sqlite3.Connection, message_id: str, imported: list[str]) -> None:
    """历史按 v1 顺序排列，已有 v2 独立版本留在尾部，保证最新内容不被覆盖。"""
    rows = target.execute(
        """SELECT v.id, EXISTS(SELECT 1 FROM v1_import_map p
               WHERE p.entity_kind='version' AND p.target_id=v.id) AS imported
           FROM message_versions v WHERE message_id=? ORDER BY version_number""",
        (message_id,),
    ).fetchall()
    ids = set(imported)
    historical = [str(row["id"]) for row in rows if row["imported"] and row["id"] not in ids]
    local = [str(row["id"]) for row in rows if not row["imported"]]
    # 临时负数避免重新编号时触发 (message_id, version_number) 唯一约束；仅在事务内可见。
    target.execute(
        "UPDATE message_versions SET version_number=-version_number WHERE message_id=?",
        (message_id,),
    )
    for number, version_id in enumerate(dict.fromkeys([*historical, *imported, *local]), 1):
        target.execute(
            "UPDATE message_versions SET version_number=? WHERE id=?", (number, version_id)
        )


def import_messages(
    source: sqlite3.Connection, target: sqlite3.Connection, counts: dict[str, int]
) -> None:
    """导入消息和版本，复用已存在的来源/外部标识组合，保留原文与历史顺序。"""
    for message in source.execute("SELECT * FROM messages ORDER BY collected_ts,id"):
        source_id = mapped(target, "source", str(message["source_id"]))
        message_id = mapped(target, "message", str(message["id"]))
        existing = (
            target.execute(
                "SELECT id FROM messages WHERE source_id=? AND external_id=?",
                (source_id, message["external_id"]),
            ).fetchone()
            if message_id is None
            else None
        )
        created = message_id is None and existing is None
        message_id = message_id or (
            str(existing[0]) if existing else legacy_uuid("message", str(message["id"]))
        )
        record(counts, "messages", created)
        revisions = source.execute(
            "SELECT * FROM message_revisions WHERE message_id=? ORDER BY observed_ts,rowid",
            (message["id"],),
        ).fetchall()
        if created:
            times = [int(row["observed_ts"]) for row in revisions] + [
                int(message["collected_ts"] or 0)
            ]
            target.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                (message_id, source_id, message["external_id"], min(times), max(times)),
            )
        remember(target, "message", str(message["id"]), message_id)
        _import_history(target, message, message_id, revisions, counts)


def _import_history(
    target: sqlite3.Connection,
    message: sqlite3.Row,
    message_id: str,
    revisions: list[sqlite3.Row],
    counts: dict[str, int],
) -> None:
    """处理没有历史版本或当前快照不同的旧消息，不丢失最新原文。"""
    ordered: list[str] = []
    changed = False
    for revision in revisions:
        version_id, created = _version(
            target,
            message_id,
            str(revision["id"]),
            _values(message, revision),
            message["language"],
            counts,
        )
        ordered.append(version_id)
        changed |= created
    current = _values(message, None)
    if not revisions or _values(message, revisions[-1])[:5] != current[:5]:
        digest = hashlib.sha256(repr(current[:5]).encode()).hexdigest()
        version_id, created = _version(
            target,
            message_id,
            f"current:{message['id']}:{digest}",
            current,
            message["language"],
            counts,
        )
        ordered.append(version_id)
        changed |= created
    if changed:
        _order_versions(target, message_id, ordered)
