"""v1 导入的只读快照、标识映射与报告计数。"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from rss_v2.domain import DomainError


def legacy_uuid(kind: str, legacy_id: str) -> str:
    """从 v1 标识生成稳定 UUID；复制源数据库后仍保持同一标识。"""
    return str(uuid5(NAMESPACE_URL, f"rss-v2/import-v1/{kind}/{legacy_id}"))


def mapped(connection: sqlite3.Connection, kind: str, legacy_id: str) -> str | None:
    """读取旧标识映射，避免来源改名或改 URL 后重复导入。"""
    row = connection.execute(
        "SELECT target_id FROM v1_import_map WHERE entity_kind=? AND legacy_id=?",
        (kind, legacy_id),
    ).fetchone()
    return str(row[0]) if row else None


def remember(connection: sqlite3.Connection, kind: str, legacy_id: str, target_id: str) -> None:
    """记录首次映射，不改写已有映射。"""
    connection.execute(
        "INSERT OR IGNORE INTO v1_import_map VALUES(?,?,?)", (kind, legacy_id, target_id)
    )


def record(counts: dict[str, int], kind: str, created: bool) -> None:
    """累计新增或复用数量。"""
    key = f"{kind}_{'created' if created else 'reused'}"
    counts[key] += 1


@contextmanager
def readonly_snapshot(path: Path) -> Generator[sqlite3.Connection, None, None]:
    """只读打开源库并包含 WAL 生成内存快照；不会执行源库升级。"""
    if not path.is_file():
        raise DomainError("import_source_missing", "源数据库不存在")
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            source.backup(snapshot)
        finally:
            source.close()
        snapshot.execute("PRAGMA query_only=ON")
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DomainError("import_source_corrupt", "源数据库完整性检查失败")
        yield snapshot
    except sqlite3.Error as exc:
        raise DomainError("import_database_error", "导入数据库读取或写入失败") from exc
    finally:
        snapshot.close()


def validate_v1(connection: sqlite3.Connection) -> None:
    """校验本项目 v1 RSS 表结构与引用，异常数据必须显式失败。"""
    required = {
        "sources": {
            "id",
            "name",
            "url",
            "category",
            "via",
            "enabled",
            "platform",
            "feed_title",
            "feed_link",
            "metadata_json",
            "created_ts",
            "updated_ts",
        },
        "messages": {
            "id",
            "source_id",
            "external_id",
            "title",
            "summary",
            "content",
            "url",
            "published_ts",
            "collected_ts",
            "language",
        },
        "message_revisions": {
            "id",
            "message_id",
            "title",
            "summary",
            "content",
            "source_url",
            "published_ts",
            "observed_ts",
            "collected_ts",
        },
        "source_health": {
            "id",
            "source_id",
            "status",
            "checked_ts",
            "http_status",
            "latency_ms",
            "raw_items",
        },
    }
    for table, columns in required.items():
        actual = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns <= actual:
            raise DomainError("import_schema_unsupported", f"v1 表结构不兼容：{table}")
    for table, field, parent in (
        ("messages", "source_id", "sources"),
        ("message_revisions", "message_id", "messages"),
        ("source_health", "source_id", "sources"),
    ):
        if connection.execute(
            f"SELECT 1 FROM {table} WHERE {field} NOT IN (SELECT id FROM {parent}) LIMIT 1"
        ).fetchone():
            raise DomainError("import_orphan_record", f"v1 存在孤立记录：{table}")
