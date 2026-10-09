"""迁移原子性、历史校验和与并发启动验收。"""

import hashlib
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import respx
from httpx import Response

from rss_v2.adapters.rss.feedparser_client import HTTPXFeedClient
from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.migrations import MigrationRunner

MIGRATIONS_BEFORE_CONTENT_NORMALIZATION = (
    "0001_initial.sql",
    "0002_task_recovery.sql",
    "0003_version_and_headers.sql",
    "0004_publication_seconds.sql",
    "0005_v1_import.sql",
    "0006_provider_models.sql",
    "0007_api_keys.sql",
)


def test_fresh_database_and_checksum(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    shutil.copytree("migrations", migrations)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    assert len(runner.run()) == 11
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
    assert sum(len(result) for result in results) == 11


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


@respx.mock
def test_content_normalization_keeps_recollection_idempotent(tmp_path: Path) -> None:
    """归一"摘要复制到正文"的历史数据后，同一来源重新采集不会产生假新版本。

    迁移只清空正文、不重算指纹；指纹口径仍按摘要兜底，因此升级前写入的指纹与
    升级后采集器算出的指纹逐字节相同，`_store_item` 会把它判为未变化。
    """
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in MIGRATIONS_BEFORE_CONTENT_NORMALIZATION:
        shutil.copy(Path("migrations") / name, migrations / name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    title = "New computing platform"
    summary = "A new computing platform is available."
    with database.transaction() as connection:
        category = connection.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO rss_sources(id,name,url,language,category_id,created_at,updated_at)"
            " VALUES('s','Example Tech','https://example.test/feed.xml','en',?,1,1)",
            (category,),
        )
        connection.execute("INSERT INTO messages VALUES('m','s','entry-1',1,1)")
        connection.execute(
            "INSERT INTO message_versions VALUES('v','m',1,?,?,?,'https://example.test/entry-1',"
            "1791331200,1,'en',?)",
            (
                title,
                summary,
                summary,
                hashlib.sha256(f"{title}\n{summary}\n{summary}".encode()).hexdigest(),
            ),
        )
    shutil.copy(
        Path("migrations") / "0008_normalize_content.sql", migrations / "0008_normalize_content.sql"
    )
    assert runner.run() == ["0008_normalize_content.sql"]
    feed = Path("tests/fixtures/sample_feed.xml").read_bytes()
    respx.get("https://example.test/feed.xml").mock(return_value=Response(200, content=feed))
    item = HTTPXFeedClient().fetch("https://example.test/feed.xml", 20.0).items[0]
    connection = database.connect()
    try:
        version = connection.execute("SELECT * FROM message_versions").fetchone()
        assert item.title == title and item.summary == summary
        assert version["content"] == ""
        assert version["content_hash"] == item.content_hash
    finally:
        connection.close()


def test_provider_model_migration_carries_existing_single_model(tmp_path: Path) -> None:
    """旧库 Provider 的单模型字段升级后成为一条模型行，且列被移除。"""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in ("0001_initial.sql", "0002_task_recovery.sql", "0003_version_and_headers.sql"):
        shutil.copy(Path("migrations") / name, migrations / name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO llm_providers(id,name,base_url,model,enabled,priority,timeout_seconds,session_header_name,created_at,updated_at,extra_headers_json)"
            " VALUES('p','旧服务','https://llm.test/v1','deepseek-v4.1-flash',0,100,60,NULL,10,20,'{}')"
        )
    shutil.copy(
        Path("migrations") / "0006_provider_models.sql", migrations / "0006_provider_models.sql"
    )
    assert runner.run() == ["0006_provider_models.sql"]
    connection = database.connect()
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(llm_providers)")}
        assert "model" not in columns
        model = connection.execute("SELECT * FROM llm_provider_models").fetchone()
        assert model["provider_id"] == "p" and model["model"] == "deepseek-v4.1-flash"
        assert model["enabled"] == 0 and model["priority"] == 100
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()


def test_enrichment_migration_extends_tasks_and_preserves_rows(tmp_path: Path) -> None:
    """加工任务类型加入 CHECK 约束；重建 tasks 表不丢失租约与重试字段。"""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in MIGRATIONS_BEFORE_CONTENT_NORMALIZATION:
        shutil.copy(Path("migrations") / name, migrations / name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,lease_until,"
            "payload_json,created_at,updated_at,lease_token,available_at)"
            " VALUES('t','translate_message','k','running',2,999,'{}',10,20,'token',30)"
        )
    for name in ("0008_normalize_content.sql", "0009_enrichment.sql"):
        shutil.copy(Path("migrations") / name, migrations / name)
    assert runner.run() == ["0008_normalize_content.sql", "0009_enrichment.sql"]
    connection = database.connect()
    try:
        task = connection.execute("SELECT * FROM tasks").fetchone()
        assert task["idempotency_key"] == "k" and task["status"] == "running"
        assert task["attempts"] == 2 and task["lease_token"] == "token"
        assert task["available_at"] == 30
        # 新任务类型能写入；旧约束会拒绝这一行。
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,payload_json,"
            "created_at,updated_at) VALUES('e','enrich_message','k2','queued',0,'{}',10,20)"
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(message_enrichments)")
        }
        assert {"keywords_json", "message_version_id", "prompt_version"} <= columns
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(tasks)")}
        assert {"idx_tasks_claim", "idx_tasks_available"} <= indexes
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()


def test_api_key_migration_rebuilds_keys_and_renames_audit_columns(tmp_path: Path) -> None:
    """密钥表改为保存密钥值：旧引用行不迁移，审计与译文列改名为掩码列。"""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in (
        "0001_initial.sql",
        "0002_task_recovery.sql",
        "0003_version_and_headers.sql",
        "0004_publication_seconds.sql",
        "0005_v1_import.sql",
        "0006_provider_models.sql",
    ):
        shutil.copy(Path("migrations") / name, migrations / name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO llm_providers(id,name,base_url,enabled,timeout_seconds,created_at,updated_at,extra_headers_json)"
            " VALUES('p','服务','https://llm.test/v1',1,60,10,20,'{}')"
        )
        connection.execute(
            "INSERT INTO llm_provider_keys(id,provider_id,key_ref,priority,enabled,created_at,updated_at)"
            " VALUES('k','p','MAIN',100,1,10,20)"
        )
        connection.execute(
            "INSERT INTO llm_calls(id,task_id,provider_id,key_ref,model,prompt_version,input_hash,duration_ms,token_usage_json,status,created_at)"
            " VALUES('c',NULL,'p','MAIN','m','v1','hash',1,'{}','succeeded',10)"
        )
    shutil.copy(Path("migrations") / "0007_api_keys.sql", migrations / "0007_api_keys.sql")
    assert runner.run() == ["0007_api_keys.sql"]
    connection = database.connect()
    try:
        key_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(llm_provider_keys)")
        }
        assert "secret" in key_columns and "key_ref" not in key_columns
        # 旧引用行没有可兑换的密钥值，迁移后不保留占位数据。
        assert not connection.execute("SELECT * FROM llm_provider_keys").fetchall()
        call_columns = {row["name"] for row in connection.execute("PRAGMA table_info(llm_calls)")}
        assert "key_masked" in call_columns and "key_ref" not in call_columns
        assert connection.execute("SELECT key_masked FROM llm_calls").fetchone()["key_masked"] == (
            "MAIN"
        )
        translation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(translations)")
        }
        assert "key_masked" in translation_columns
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
