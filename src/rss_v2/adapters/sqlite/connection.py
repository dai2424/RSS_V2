"""SQLite 连接和事务辅助。"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from rss_v2.domain import ConflictError


class SQLiteDatabase:
    """按请求或任务创建短生命周期 SQLite 连接。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """创建启用 WAL 和外键的连接。"""

        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        self._execute_with_retry(connection, "PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """提供可回滚事务。"""

        connection = self.connect()
        try:
            self._execute_with_retry(connection, "BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ConflictError("conflict", "数据存在重复或引用无效，请检查后重试") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _execute_with_retry(connection: sqlite3.Connection, statement: str) -> None:
        """WAL 首次初始化可能不等待 busy_timeout，只重试锁竞争。"""
        for attempt in range(4):
            try:
                connection.execute(statement)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 3:
                    raise
                time.sleep(0.05 * 2**attempt)

    def backup(self, destination: Path) -> None:
        """使用 SQLite 在线备份 API 保留 WAL 中的已提交数据；禁止覆盖已有备份。"""
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ConflictError("backup_exists", "备份目标已经存在")
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("备份完整性校验失败")
        finally:
            source.close()
            target.close()
