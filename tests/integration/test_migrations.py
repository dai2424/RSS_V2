"""迁移原子性、历史校验和与并发启动验收。"""

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.migrations import MigrationRunner


def test_fresh_database_and_checksum(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    shutil.copytree("migrations", migrations)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    assert len(runner.run()) == 5
    assert runner.run() == []
    path = migrations / "0001_initial.sql"
    path.write_text(path.read_text(encoding="utf-8") + "\n-- mutation\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="已被修改"):
        runner.run()


def test_failed_ddl_rolls_back_completely(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        "CREATE TABLE partial(id INTEGER);\nINSERT INTO absent VALUES(1);\n", encoding="utf-8"
    )
    database = SQLiteDatabase(tmp_path / "rss.db")
    with pytest.raises(sqlite3.OperationalError):
        MigrationRunner(database, migrations).run()
    connection = database.connect()
    try:
        assert not connection.execute("SELECT * FROM sqlite_master WHERE name='partial'").fetchall()
        assert not connection.execute("SELECT * FROM schema_migrations").fetchall()
    finally:
        connection.close()


def test_two_processes_do_not_apply_migration_twice(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "rss.db")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: MigrationRunner(database, Path("migrations")).run(), range(2))
        )
    assert sum(len(result) for result in results) == 5


def test_upgrade_preserves_versions_and_translations(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in ("0001_initial.sql", "0002_task_recovery.sql"):
        shutil.copy(Path("migrations") / name, migrations / name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        category = connection.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO rss_sources(id,name,url,language,category_id,created_at,updated_at) VALUES('s','source','https://example.test/feed','en',?,1,1)",
            (category,),
        )
        connection.execute("INSERT INTO messages VALUES('m','s','guid',1,1)")
        connection.execute(
            "INSERT INTO message_versions VALUES('v','m',1,'title','summary','content','https://example.test/news','2026-10-07T00:00:00+00:00',1,'en','hash')"
        )
        connection.execute(
            "INSERT INTO translations(id,message_version_id,status,title,summary,content,model,prompt_version,created_at,updated_at) VALUES('t','v','succeeded','译文','摘要','正文','model','v1',1,1)"
        )
    for name in ("0003_version_and_headers.sql", "0004_publication_seconds.sql"):
        shutil.copy(Path("migrations") / name, migrations / name)
    assert len(runner.run()) == 2
    connection = database.connect()
    try:
        version = connection.execute("SELECT * FROM message_versions").fetchone()
        translation = connection.execute("SELECT * FROM translations").fetchone()
        assert version["published_at"] == 1791331200
        assert translation["message_version_id"] == version["id"] and translation["title"] == "译文"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
