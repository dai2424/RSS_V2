"""v1 导入预览、只读保护、历史保留、幂等、合并和原子回滚验收。"""

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from rss_v2.adapters.sqlite import v1_import
from rss_v2.api.app import create_app
from rss_v2.bootstrap import Container, build_container
from rss_v2.domain import DomainError, Message, MessageVersion, SourceLanguage
from rss_v2.domain.values import new_id
from rss_v2.settings import Settings

SECRET = "test-secret-must-not-be-imported"


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    """模拟 v1 的历史字段、重复内容、缺 URL、无版本消息和敏感错误。"""
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE sources(
                id TEXT PRIMARY KEY,name TEXT,url TEXT,category TEXT,via TEXT,
                enabled INTEGER,platform TEXT,feed_title TEXT,feed_link TEXT,
                metadata_json TEXT,created_ts INTEGER,updated_ts INTEGER);
            CREATE TABLE messages(
                id TEXT PRIMARY KEY,source_id TEXT,external_id TEXT,title TEXT,
                summary TEXT,content TEXT,url TEXT,published_ts INTEGER,
                collected_ts INTEGER,language TEXT);
            CREATE TABLE message_revisions(
                id TEXT PRIMARY KEY,message_id TEXT,title TEXT,summary TEXT,
                content TEXT,source_url TEXT,published_ts INTEGER,
                observed_ts INTEGER,collected_ts INTEGER);
            CREATE TABLE source_health(
                id TEXT PRIMARY KEY,source_id TEXT,status TEXT,checked_ts INTEGER,
                http_status INTEGER,latency_ms INTEGER,raw_items INTEGER,error TEXT);
        """)
        connection.executemany(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "s",
                    "原来源",
                    "https://example.test/feed",
                    "旧分类",
                    "rsshub-mirror",
                    1,
                    "rss",
                    "feed title",
                    "https://example.test/",
                    json.dumps({"aliases": ["别名"], "api_key": SECRET}),
                    1,
                    40,
                ),
                ("missing", "缺地址", None, "", None, 1, "rss", None, None, "{}", 1, 40),
            ],
        )
        connection.executemany(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "m",
                    "s",
                    "guid",
                    "Title A",
                    "Summary",
                    "Body A",
                    "https://example.test/item?utm_source=legacy",
                    9,
                    30,
                    None,
                ),
                (
                    "unversioned",
                    "s",
                    "other",
                    "中文标题",
                    "摘要",
                    "中文正文",
                    "https://example.test/other",
                    None,
                    35,
                    None,
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO message_revisions VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("r-z", "m", "Title A", "Summary", "Body A", "", 9, 10, None),
                ("r-y", "m", "Title B", "Summary", "Body B", "", 9, 20, 20),
                ("r-x", "m", "Title A", "Summary", "Body A", "", 9, 20, 30),
            ],
        )
        connection.executemany(
            "INSERT INTO source_health VALUES(?,?,?,?,?,?,?,?)",
            [
                ("h1", "s", "ok", 30, 200, 100, 2, None),
                ("h2", "s", "failed", 40, 500, 200, 0, SECRET),
            ],
        )
    return path


@pytest.fixture
def target(tmp_path: Path) -> Container:
    """通过生产组合根创建临时目标库。"""
    return build_container(Settings(runtime_dir=tmp_path / "target"))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preview_never_changes_either_database(legacy: Path, target: Container) -> None:
    source_hash, target_hash = _hash(legacy), _hash(target.database.path)
    report = target.import_v1(legacy)
    assert report["applied"] is False and report["backup_path"] is None
    assert report["counts"]["sources_created"] == 2
    assert report["counts"]["messages_created"] == 2
    assert report["counts"]["versions_created"] == 4
    assert _hash(legacy) == source_hash and _hash(target.database.path) == target_hash
    assert target.source_service.list() == []


def test_preview_does_not_create_target_database(legacy: Path, tmp_path: Path) -> None:
    container = build_container(Settings(runtime_dir=tmp_path / "absent"), migrate=False)
    assert not container.database.path.exists()
    assert container.import_v1(legacy)["counts"]["messages_created"] == 2
    assert not container.database.path.exists()


def test_apply_retains_history_and_is_repeatable(legacy: Path, target: Container) -> None:
    original_hash = _hash(legacy)
    report = target.import_v1(legacy, apply=True)
    backup = Path(report["backup_path"])
    assert backup.exists() and report["sources_without_url"] == ["missing"]
    restored = build_container(Settings(runtime_dir=backup.parent, database_path=backup))
    assert restored.source_service.list() == []
    sources = target.source_service.list()
    source = next(item for item in sources if item.url)
    missing = next(item for item in sources if not item.url)
    assert not missing.enabled
    with pytest.raises(DomainError, match="HTTP"):
        target.source_service.update(missing.id, {"enabled": True})
    messages = target.messages.list_messages(source_id=source.id)
    message = next(item[0] for item in messages if item[0].external_id == "guid")
    versions = list(reversed(target.messages.versions(message.id)))
    assert [version.content for version in versions] == ["Body A", "Body B", "Body A"]
    assert [version.version_number for version in versions] == [1, 2, 3]
    assert versions[0].collected_at == 10 and versions[-1].collected_at == 30
    assert versions[-1].url == "https://example.test/item"
    assert versions[-1].language == SourceLanguage.ENGLISH
    assert versions[0].content_hash == versions[-1].content_hash
    for item in [*sources, *versions, message]:
        UUID(item.id)
    assert len(target.source_service.health_history(source.id)) == 2
    repeated = target.import_v1(legacy, apply=True)
    assert all(value == 0 for key, value in repeated["counts"].items() if key.endswith("created"))
    assert _hash(legacy) == original_hash
    with TestClient(create_app(target.settings)) as client:
        assert client.get("/api/sources").json()["total"] == 2
        assert len(client.get("/api/messages").json()["items"]) == 2
    assert SECRET.encode() not in target.database.path.read_bytes()
    assert SECRET not in json.dumps(source.metadata)


def test_merges_source_and_preserves_existing_v2_content(legacy: Path, target: Container) -> None:
    category = target.category_service.list()[0]
    source = target.source_service.create(
        "v2 名称",
        "https://example.test/feed?utm_source=v2",
        "rss",
        SourceLanguage.ENGLISH,
        category.id,
        60,
    )
    message = target.messages.upsert_message(Message(new_id(), source.id, "guid", 100, 100))
    local = target.messages.add_version(
        MessageVersion(
            new_id(),
            message.id,
            1,
            "v2 edit",
            "v2 summary",
            "v2 content",
            "https://example.test/item",
            90,
            100,
            SourceLanguage.ENGLISH,
            "v2-hash",
        )
    )
    report = target.import_v1(legacy, apply=True)
    assert report["counts"]["sources_created"] == 1
    assert report["counts"]["sources_reused"] == 1
    assert target.source_service.get(source.id) == source
    assert target.messages.latest_version(message.id).id == local.id
    assert target.messages.latest_version(message.id).version_number == 4
    target.source_service.update(source.id, {"name": "用户编辑", "url": "https://example.test/new"})
    repeated = target.import_v1(legacy, apply=True)
    assert repeated["counts"]["sources_created"] == 0
    assert target.source_service.get(source.id).name == "用户编辑"
    assert target.messages.latest_version(message.id).id == local.id


def test_source_wal_and_append_import(legacy: Path, target: Container) -> None:
    target.import_v1(legacy, apply=True)
    connection = sqlite3.connect(legacy)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "UPDATE messages SET title='Title C',content='Body C',collected_ts=50 WHERE id='m'"
        )
        connection.execute(
            "INSERT INTO message_revisions VALUES"
            "('r-c','m','Title C','Summary','Body C','',9,50,50)"
        )
        connection.commit()
        assert Path(str(legacy) + "-wal").exists()
        report = target.import_v1(legacy, apply=True)
        assert report["counts"]["messages_created"] == 0
        assert report["counts"]["versions_created"] == 1
        message = next(
            row[0] for row in target.messages.list_messages() if row[0].external_id == "guid"
        )
        assert target.messages.latest_version(message.id).content == "Body C"
        assert target.import_v1(legacy)["counts"]["versions_created"] == 0
    finally:
        connection.close()


def test_incompatible_and_same_database_are_rejected(legacy: Path, target: Container) -> None:
    with pytest.raises(DomainError, match="不能相同"):
        target.import_v1(target.database.path, apply=True)
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP TABLE message_revisions")
    with pytest.raises(DomainError, match="表结构不兼容"):
        target.import_v1(legacy, apply=True)
    assert target.source_service.list() == []


def test_write_failure_rolls_back_every_record(
    legacy: Path, target: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = v1_import.import_messages

    def fail_after_messages(
        source: sqlite3.Connection, destination: sqlite3.Connection, counts: dict[str, int]
    ) -> None:
        original(source, destination, counts)
        path = Path(destination.execute("PRAGMA database_list").fetchone()[2])
        if path.resolve() == target.database.path.resolve():
            raise DomainError("fixture_failure", "模拟消息写入后异常")

    monkeypatch.setattr(v1_import, "import_messages", fail_after_messages)
    with pytest.raises(DomainError):
        target.import_v1(legacy, apply=True)
    assert target.source_service.list() == []
    with target.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM v1_import_map").fetchone()[0] == 0
