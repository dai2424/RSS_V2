"""纯 SQL 迁移执行器。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rss_v2.adapters.sqlite.connection import SQLiteDatabase


class MigrationRunner:
    """执行编号递增且不可变的 SQL 迁移。"""

    def __init__(self, database: SQLiteDatabase, migrations_dir: Path) -> None:
        self.database = database
        self.migrations_dir = migrations_dir

    def run(self) -> list[str]:
        """执行未运行迁移并返回本次执行的文件名。"""

        self.database.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.database.connect()
        applied: list[str] = []
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at INTEGER NOT NULL
                )
                """
            )
            migration_files = sorted(self.migrations_dir.glob("*.sql"))
            for migration in migration_files:
                checksum = hashlib.sha256(migration.read_bytes()).hexdigest()
                row = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ?", (migration.name,)
                ).fetchone()
                if row is not None:
                    if row["checksum"] != checksum:
                        raise RuntimeError(f"迁移文件已被修改：{migration.name}")
                    continue
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.executescript(migration.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, strftime('%s', 'now'))",
                        (migration.name, checksum),
                    )
                    connection.commit()
                    applied.append(migration.name)
                except Exception:
                    connection.rollback()
                    raise
            return applied
        finally:
            connection.close()
