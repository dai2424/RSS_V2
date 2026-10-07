"""独立 v1 RSS 导入：隔离预览、在线备份及原子写入。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.migrations import MigrationRunner
from rss_v2.adapters.sqlite.v1_common import (
    legacy_uuid,
    mapped,
    readonly_snapshot,
    record,
    remember,
    validate_v1,
)
from rss_v2.adapters.sqlite.v1_messages import import_messages
from rss_v2.domain import DomainError
from rss_v2.domain.values import normalize_url, validate_http_url


class SQLiteV1Importer:
    """只导入 RSS 领域数据，映射防止重复写入及覆盖 v2 编辑。"""

    def __init__(self, database: SQLiteDatabase, migrations_dir: Path, runtime_dir: Path) -> None:
        self.database = database
        self.migrations_dir = migrations_dir
        self.runtime_dir = runtime_dir

    def run(self, source_path: Path, apply: bool = False) -> dict[str, Any]:
        """默认在临时数据库预览；确认应用时先备份再升级并原子导入。"""
        if source_path.resolve() == self.database.path.resolve() or (
            source_path.exists()
            and self.database.path.exists()
            and source_path.samefile(self.database.path)
        ):
            raise DomainError("import_same_database", "源数据库与目标数据库不能相同")
        with readonly_snapshot(source_path) as source:
            validate_v1(source)
            with TemporaryDirectory(prefix="rss-v2-import-") as temporary:
                preview = SQLiteDatabase(Path(temporary) / "preview.db")
                if self.database.path.is_file():
                    with readonly_snapshot(self.database.path) as current:
                        connection = sqlite3.connect(preview.path)
                        try:
                            current.backup(connection)
                        finally:
                            connection.close()
                MigrationRunner(preview, self.migrations_dir).run()
                report = self._transfer(source, preview)
            report["applied"] = apply
            report["backup_path"] = None
            if apply:
                if self.database.path.is_file():
                    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                    backup = (
                        self.runtime_dir / "db/backups" / f"before-v1-{stamp}-{uuid4().hex[:8]}.db"
                    )
                    self.database.backup(backup)
                    report["backup_path"] = str(backup.resolve())
                MigrationRunner(self.database, self.migrations_dir).run()
                actual = self._transfer(source, self.database)
                report.update(actual)
            return report

    def _transfer(self, source: sqlite3.Connection, database: SQLiteDatabase) -> dict[str, Any]:
        """所有领域记录与标识映射共用一个可回滚事务。"""
        counts = {
            f"{kind}_{state}": 0
            for kind in ("categories", "sources", "messages", "versions", "health")
            for state in ("created", "reused")
        }
        missing_urls: list[str] = []
        with database.transaction() as target:
            for row in source.execute("SELECT * FROM sources ORDER BY created_ts,id"):
                self._source(target, row, counts)
                if not row["url"]:
                    missing_urls.append(str(row["id"]))
            import_messages(source, target, counts)
            for row in source.execute("SELECT * FROM source_health ORDER BY checked_ts,id"):
                self._health(target, row, counts)
            if target.execute("PRAGMA foreign_key_check").fetchone():
                raise DomainError("import_invalid_reference", "导入后外键检查失败")
        return {"counts": counts, "sources_without_url": missing_urls}

    @staticmethod
    def _category(target: sqlite3.Connection, row: sqlite3.Row, counts: dict[str, int]) -> str:
        """保留原行业名称，优先复用同名的启用分类。"""
        name = str(row["category"] or "未分类").strip() or "未分类"
        category_id = mapped(target, "category", name)
        if category_id:
            return category_id
        existing = target.execute(
            "SELECT id FROM categories WHERE name=? AND is_active=1 ORDER BY id LIMIT 1", (name,)
        ).fetchone()
        category_id = str(existing[0]) if existing else legacy_uuid("category", name)
        record(counts, "categories", existing is None)
        if existing is None:
            target.execute(
                "INSERT INTO categories VALUES(?,?,?,?,?,?,?)",
                (
                    category_id,
                    name,
                    f"v1-{category_id}",
                    0,
                    1,
                    row["created_ts"],
                    row["updated_ts"],
                ),
            )
        remember(target, "category", name, category_id)
        return category_id

    def _source(self, target: sqlite3.Connection, row: sqlite3.Row, counts: dict[str, int]) -> None:
        """按 Feed URL 复用来源，保留 v2 修改；缺地址来源保持停用。"""
        legacy_id = str(row["id"])
        source_id = mapped(target, "source", legacy_id)
        if source_id:
            record(counts, "sources", False)
            return
        url = normalize_url(validate_http_url(str(row["url"]))) if row["url"] else ""
        existing = next(
            (
                item
                for item in target.execute("SELECT id,url FROM rss_sources")
                if url and normalize_url(str(item["url"])) == url
            ),
            None,
        )
        source_id = str(existing["id"]) if existing else legacy_uuid("source", legacy_id)
        record(counts, "sources", existing is None)
        if existing is None:
            category_id = self._category(target, row, counts)
            metadata = self._metadata(row)
            target.execute(
                """INSERT INTO rss_sources(id,name,url,platform,language,category_id,enabled,
                       feed_title,feed_link,source_type,metadata_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source_id,
                    row["name"],
                    url,
                    row["platform"],
                    "auto",
                    category_id,
                    int(bool(row["enabled"]) and bool(url)),
                    row["feed_title"],
                    row["feed_link"],
                    "rss",
                    metadata,
                    row["created_ts"],
                    row["updated_ts"],
                ),
            )
        remember(target, "source", legacy_id, source_id)

    @staticmethod
    def _metadata(row: sqlite3.Row) -> str:
        """扩展元数据采用明确白名单，不复制认证字段或任意旧配置。"""
        try:
            metadata = json.loads(row["metadata_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise DomainError("import_invalid_metadata", "v1 来源元数据不是有效 JSON") from exc
        if not isinstance(metadata, dict):
            raise DomainError("import_invalid_metadata", "v1 来源元数据必须是对象")
        safe: dict[str, Any] = {
            key: metadata[key] for key in ("aliases", "input_section") if key in metadata
        }
        safe["v1_source_id"] = str(row["id"])
        safe["v1_via"] = row["via"]
        return json.dumps(safe, ensure_ascii=False)

    @staticmethod
    def _health(target: sqlite3.Connection, row: sqlite3.Row, counts: dict[str, int]) -> None:
        """保留历史健康状态和计数，原始错误文本不写入 v2。"""
        legacy_id = str(row["id"])
        existing = mapped(target, "health", legacy_id)
        record(counts, "health", existing is None)
        if existing:
            return
        health_id = legacy_uuid("health", legacy_id)
        success = row["status"] in {"ok", "empty", "not_modified"}
        target.execute(
            "INSERT INTO source_health_checks VALUES(?,?,?,?,?,?,?,?,?)",
            (
                health_id,
                mapped(target, "source", str(row["source_id"])),
                row["checked_ts"],
                row["http_status"],
                row["latency_ms"],
                int(success),
                row["raw_items"],
                None if success else "v1_health_failure",
                None if success else "v1 历史采集未成功",
            ),
        )
        remember(target, "health", legacy_id, health_id)
