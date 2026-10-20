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
    assert len(runner.run()) == 30
    assert runner.run() == []
    path = migrations / "0001_initial.sql"
    path.write_text(path.read_text(encoding="utf-8") + "\n-- mutation\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="已被修改"):
        runner.run()


def test_keyword_tables_and_structured_prompt_seed(tmp_path: Path) -> None:
    """关键词关系表与结构化提示词版本：升级后启用版本换新，旧版本归档保留。"""

    migrations = tmp_path / "migrations"
    shutil.copytree("migrations", migrations)
    database = SQLiteDatabase(tmp_path / "rss.db")
    MigrationRunner(database, migrations).run()
    connection = database.connect()
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(enrichment_keywords)")
        }
        assert {
            "enrichment_id",
            "message_version_id",
            "ordinal",
            "raw",
            "normalized",
            "kind",
            "reused",
        } <= columns
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(enrichment_keywords)")
        }
        assert {"idx_enrichment_keywords_normalized", "idx_enrichment_keywords_version"} <= indexes
        active = connection.execute(
            "SELECT version, user_template FROM llm_prompts"
            " WHERE task_kind='enrich_message' AND status='active'"
        ).fetchall()
        assert len(active) == 1 and active[0]["version"] == 4
        # 旧版本仍可回溯，历史结果的 prompt_version 不会指向不存在的记录。
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM llm_prompts WHERE prompt_key='enrich' AND status='archived'"
            ).fetchone()[0]
            == 3
        )
        assert "kind" in active[0]["user_template"]
        assert "{{vocabulary}}" in active[0]["user_template"]
        tasks_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='tasks'"
        ).fetchone()[0]
        assert "keyword_merge_suggest" in str(tasks_ddl)
        assert "discover_events" in str(tasks_ddl)
        assert "prepare_materials" in str(tasks_ddl)
        assert "digest_materials" in str(tasks_ddl)
        # 常驻建议：一条建议就是一个词，与合并建议形状不同，因此单独一张表。
        assert {
            row["name"] for row in connection.execute("PRAGMA table_info(keyword_pin_suggestions)")
        } == {"key", "text", "rationale", "status", "weeks", "created_at", "resolved_at"}
        pin_prompt = connection.execute(
            "SELECT version, status, user_template FROM llm_prompts"
            " WHERE task_kind='keyword_pin_suggest'"
        ).fetchall()
        assert len(pin_prompt) == 1 and pin_prompt[0]["status"] == "active"
        assert "{{vocabulary}}" in pin_prompt[0]["user_template"]
        suggest = connection.execute(
            "SELECT version, user_template FROM llm_prompts"
            " WHERE task_kind='keyword_merge_suggest' AND status='active'"
        ).fetchall()
        assert len(suggest) == 1 and suggest[0]["version"] == 2
        assert "最多给 8 组" in suggest[0]["user_template"]
        # 常驻词表与合并记录里的搬运事实：撤销时据此还原标记。
        assert {row["name"] for row in connection.execute("PRAGMA table_info(keyword_pins)")} == {
            "key",
            "created_at",
        }
        assert {
            "pinned_json",
            "target_was_pinned",
        } <= {row["name"] for row in connection.execute("PRAGMA table_info(keyword_merges)")}
        # 合并建议按词记账：扫过的写法不重扫。
        assert {
            row["name"]
            for row in connection.execute("PRAGMA table_info(keyword_suggestion_scanned)")
        } == {"normalized", "scanned_at"}
        # 别名与合并记录：别名指向最终规范词，合并记录保留撤销时间。
        alias_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(keyword_aliases)")
        }
        assert {"alias_norm", "canonical_norm", "merge_id", "created_at"} <= alias_columns
        # 撤销改为按"每次合并的独立变更记录"回放：一层历史的旧列已删（它是丢关系的根因）。
        assert "previous_canonical" not in alias_columns
        assert "previous_merge_id" not in alias_columns
        assert {
            row["name"] for row in connection.execute("PRAGMA table_info(keyword_alias_changes)")
        } == {"merge_id", "alias_norm", "previous_canonical", "previous_merge_id"}
        merge_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(keyword_merges)")
        }
        assert {"target_norm", "target_raw", "members_json", "affected_messages", "undone_at"} <= (
            merge_columns
        )
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()


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
    assert sum(len(result) for result in results) == 30


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


def test_materials_migration_upgrades_discovery_library(tmp_path: Path) -> None:
    """0025 → 0026：项目补资料摘要列、资料表可用、任务类型接受 prepare_materials。"""

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in sorted(Path("migrations").glob("*.sql")):
        if path.name <= "0025_discovery.sql":
            shutil.copy(path, migrations / path.name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        category = connection.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO rss_sources(id,name,url,language,category_id,created_at,updated_at)"
            " VALUES('s','Example Tech','https://example.test/feed.xml','en',?,1,1)",
            (category,),
        )
        connection.execute(
            "INSERT INTO leads(id,title,note,created_at,updated_at,kind)"
            " VALUES('lead-1','选题事件',NULL,10,10,'discovery')"
        )
        connection.execute(
            "INSERT INTO writing_projects(id,lead_id,candidate_id,title,status,created_at,updated_at)"
            " VALUES('p1','lead-1',NULL,'选题事件','materials_pending',10,10)"
        )
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,created_at,updated_at)"
            " VALUES('t1','discover_events','k1','succeeded',1,10,10)"
        )

    shutil.copy(Path("migrations") / "0026_materials.sql", migrations / "0026_materials.sql")
    assert runner.run() == ["0026_materials.sql"]
    connection = database.connect()
    try:
        project = connection.execute("SELECT * FROM writing_projects WHERE id='p1'").fetchone()
        assert project is not None and project["materials_summary_json"] == "{}"
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "project_materials" in tables
        # 新任务类型被接受，非法类型仍被 CHECK 拒绝。
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,created_at,updated_at)"
            " VALUES('t2','prepare_materials','k2','queued',0,10,10)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,"
                "created_at,updated_at)"
                " VALUES('t3','compose_lead_draft','k3','queued',0,10,10)"
            )
        # 资料行的内容指纹在同一项目内唯一（空指纹不受约束）。
        connection.execute(
            "INSERT INTO project_materials(id,project_id,kind,status,url,title,fetched_at,"
            "content_hash,created_at) VALUES('m1','p1','web','read','https://a.test/1','A',10,'h1',10)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO project_materials(id,project_id,kind,status,url,title,fetched_at,"
                "content_hash,created_at)"
                " VALUES('m2','p1','web','read','https://a.test/1','A',10,'h1',10)"
            )
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()


def test_discovery_migration_upgrades_legacy_library(tmp_path: Path) -> None:
    """0024 → 0025：线索补 kind、任务表重建不丢字段，并接受新的发现任务类型。"""

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    # 只放 0024 及之前的迁移：后续迁移要逐个升级验证，不能一次性带上（否则 0025 建的表
    # 会被更晚的迁移引用，这里就成了"跳级升级"）。
    for path in sorted(Path("migrations").glob("*.sql")):
        if path.name <= "0024_leads.sql":
            shutil.copy(path, migrations / path.name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        category = connection.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO rss_sources(id,name,url,language,category_id,created_at,updated_at)"
            " VALUES('s','Example Tech','https://example.test/feed.xml','en',?,1,1)",
            (category,),
        )
        connection.execute("INSERT INTO messages VALUES('m','s','entry-1',1,1)")
        connection.execute(
            "INSERT INTO message_versions VALUES('v','m',1,'标题','摘要','正文',"
            "'https://example.test/entry-1',1791331200,1,'en','hash-1')"
        )
        # 旧库里的线索没有 kind 列；升级后应回落为人工线索。
        connection.execute(
            "INSERT INTO leads(id,title,note,created_at,updated_at)"
            " VALUES('lead-1','人工线索',NULL,10,10)"
        )
        connection.execute(
            "INSERT INTO lead_messages(lead_id,message_version_id,added_at) VALUES('lead-1','v',10)"
        )
        # 旧任务行：重建 tasks 表时必须原样保留（含租约与重试字段）。
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,lease_until,"
            "input_version_id,output_version_id,payload_json,error_code,error_message,"
            "created_at,updated_at,lease_token,available_at)"
            " VALUES('t1','enrich_message','k1','running',2,12345,'v','v','{\"model\":\"m\"}',"
            "NULL,NULL,10,11,'token-1',7)"
        )

    shutil.copy(Path("migrations") / "0025_discovery.sql", migrations / "0025_discovery.sql")
    assert runner.run() == ["0025_discovery.sql"]
    connection = database.connect()
    try:
        lead = connection.execute("SELECT * FROM leads WHERE id='lead-1'").fetchone()
        assert lead is not None and lead["kind"] == "manual"
        task = connection.execute("SELECT * FROM tasks WHERE id='t1'").fetchone()
        assert task is not None
        assert (task["attempts"], task["lease_token"], task["available_at"]) == (2, "token-1", 7)
        assert task["payload_json"] == '{"model":"m"}'
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"discovery_batches", "discovery_candidates", "writing_projects"} <= tables
        # 新任务类型被接受，非法类型仍被 CHECK 拒绝。
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,created_at,updated_at)"
            " VALUES('t2','discover_events','k2','queued',0,10,10)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,"
                "created_at,updated_at)"
                " VALUES('t3','compose_lead_draft','k3','queued',0,10,10)"
            )
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


def test_digest_prompt_v2_archives_v1(tmp_path: Path) -> None:
    """0027 → 0028：整理提示词换到 v2 并归档 v1，旧版本仍可回溯。"""

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in sorted(Path("migrations").glob("*.sql")):
        if path.name <= "0027_digest.sql":
            shutil.copy(path, migrations / path.name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    connection = database.connect()
    try:
        before = connection.execute(
            "SELECT version,status FROM llm_prompts WHERE task_kind='digest_materials'"
        ).fetchall()
        assert [(row["version"], row["status"]) for row in before] == [(1, "active")]
    finally:
        connection.close()

    shutil.copy(
        Path("migrations") / "0028_digest_prompt_v2.sql", migrations / "0028_digest_prompt_v2.sql"
    )
    assert runner.run() == ["0028_digest_prompt_v2.sql"]
    connection = database.connect()
    try:
        rows = connection.execute(
            "SELECT version,status,user_template FROM llm_prompts"
            " WHERE task_kind='digest_materials' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["status"]) for row in rows] == [(1, "archived"), (2, "active")]
        # 新版本必须把"最多几条"和引文长度写进提示词，否则限量只是服务端截断。
        template = rows[1]["user_template"]
        assert "{{facts_per_batch}}" in template and "{{gaps_per_batch}}" in template
        assert "120 字" in template
        assert "{{facts_per_batch}}" not in rows[0]["user_template"]
    finally:
        connection.close()


def test_outline_migration_upgrades_digest_library(tmp_path: Path) -> None:
    """0028 → 0029：提纲版本表可用、任务类型接受 generate_outline、提示词种子就位。

    `tasks` 是重建表：CHECK 必须同时接受旧类型与 `generate_outline`，两个领取索引
    也必须跟着重建（漏掉索引不会报错，只会让任务领取变成全表扫描）。
    """

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in sorted(Path("migrations").glob("*.sql")):
        if path.name <= "0028_digest_prompt_v2.sql":
            shutil.copy(path, migrations / path.name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        category = connection.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO rss_sources(id,name,url,language,category_id,created_at,updated_at)"
            " VALUES('s','Example Tech','https://example.test/feed.xml','en',?,1,1)",
            (category,),
        )
        connection.execute(
            "INSERT INTO leads(id,title,note,created_at,updated_at,kind)"
            " VALUES('lead-1','选题事件',NULL,10,10,'discovery')"
        )
        connection.execute(
            "INSERT INTO writing_projects(id,lead_id,title,status,created_at,updated_at)"
            " VALUES('project-1','lead-1','选题事件','materials_ready',10,10)"
        )
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,created_at,updated_at)"
            " VALUES('task-old','digest_materials','digest:project-1:abc','succeeded',10,10)"
        )

    shutil.copy(Path("migrations") / "0029_outlines.sql", migrations / "0029_outlines.sql")
    assert runner.run() == ["0029_outlines.sql"]
    connection = database.connect()
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(outline_versions)")}
        assert {
            "id",
            "project_id",
            "version_number",
            "status",
            "source",
            "title",
            "angle",
            "sections_json",
            "material_snapshot_hash",
            "digest_run_id",
            "prompt_version",
            "model",
            "task_id",
            "based_on_version",
            "created_at",
            "confirmed_at",
        } <= columns
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(outline_versions)")}
        assert {"idx_outline_versions_project", "idx_outline_versions_snapshot"} <= indexes
        task_indexes = {row["name"] for row in connection.execute("PRAGMA index_list(tasks)")}
        assert {"idx_tasks_claim", "idx_tasks_available"} <= task_indexes
        # 重建表不能丢掉既有任务行。
        assert (
            connection.execute("SELECT COUNT(*) FROM tasks WHERE id='task-old'").fetchone()[0] == 1
        )
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,created_at,updated_at)"
            " VALUES('task-new','generate_outline','outline:project-1:abc','queued',11,11)"
        )
        # CHECK 仍要拒绝未登记的类型，否则任务表会悄悄接受没人能执行的任务。
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tasks(id,task_type,idempotency_key,status,created_at,updated_at)"
                " VALUES('task-bad','write_article','x','queued',12,12)"
            )
        prompts = connection.execute(
            "SELECT version,status,user_template FROM llm_prompts"
            " WHERE task_kind='generate_outline'"
        ).fetchall()
        assert [(row["version"], row["status"]) for row in prompts] == [(1, "active")]
        assert "sections" in prompts[0]["user_template"]
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()


def test_article_migration_upgrades_outline_library(tmp_path: Path) -> None:
    """0029 → 0030：全文版本表可用、任务类型接受 generate_article、提示词种子就位。

    `tasks` 同样是重建表：CHECK 要同时接受旧类型（含 generate_outline）与
    `generate_article`，两个领取索引也必须重建。
    """

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in sorted(Path("migrations").glob("*.sql")):
        if path.name <= "0029_outlines.sql":
            shutil.copy(path, migrations / path.name)
    database = SQLiteDatabase(tmp_path / "rss.db")
    runner = MigrationRunner(database, migrations)
    runner.run()
    with database.transaction() as connection:
        category = connection.execute("SELECT id FROM categories LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO rss_sources(id,name,url,language,category_id,created_at,updated_at)"
            " VALUES('s','Example Tech','https://example.test/feed.xml','en',?,1,1)",
            (category,),
        )
        connection.execute(
            "INSERT INTO leads(id,title,note,created_at,updated_at,kind)"
            " VALUES('lead-1','选题事件',NULL,10,10,'discovery')"
        )
        connection.execute(
            "INSERT INTO writing_projects(id,lead_id,title,status,created_at,updated_at)"
            " VALUES('project-1','lead-1','选题事件','outline_confirmed',10,10)"
        )
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,created_at,updated_at)"
            " VALUES('task-old','generate_outline','outline:project-1:abc','succeeded',10,10)"
        )

    shutil.copy(Path("migrations") / "0030_articles.sql", migrations / "0030_articles.sql")
    assert runner.run() == ["0030_articles.sql"]
    connection = database.connect()
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(article_versions)")}
        assert {
            "id",
            "project_id",
            "outline_version_id",
            "version_number",
            "kind",
            "status",
            "title",
            "lead",
            "body_json",
            "body_markdown",
            "source_list_json",
            "counters_json",
            "material_snapshot_hash",
            "digest_run_id",
            "prompt_version",
            "model",
            "task_id",
            "base_article_version_id",
            "recheck_fact_ids_json",
            "created_at",
            "saved_at",
        } <= columns
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(article_versions)")}
        assert {"idx_article_versions_project", "idx_article_versions_outline"} <= indexes
        task_indexes = {row["name"] for row in connection.execute("PRAGMA index_list(tasks)")}
        assert {"idx_tasks_claim", "idx_tasks_available"} <= task_indexes
        # 重建表不能丢掉既有任务行。
        assert (
            connection.execute("SELECT COUNT(*) FROM tasks WHERE id='task-old'").fetchone()[0] == 1
        )
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,created_at,updated_at)"
            " VALUES('task-new','generate_article','article:project-1:abc','queued',11,11)"
        )
        # 没登记进 CHECK 的类型仍要被拒绝。
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tasks(id,task_type,idempotency_key,status,created_at,updated_at)"
                " VALUES('task-bad','write_article','x','queued',12,12)"
            )
        prompts = connection.execute(
            "SELECT version,status,user_template FROM llm_prompts"
            " WHERE task_kind='generate_article'"
        ).fetchall()
        assert [(row["version"], row["status"]) for row in prompts] == [(1, "active")]
        assert "sections" in prompts[0]["user_template"]
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
