"""来源删除与级联清理集成测试。"""

import sqlite3
from pathlib import Path
from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Response

from rss_v2.bootstrap import build_container
from rss_v2.tasks.worker import Worker

CATEGORY_ID = "8cabd1f6-c0c3-4b32-863d-3836cf5a8171"


def _create_source(client: TestClient, name: str, url: str) -> str:
    response = client.post(
        "/api/sources",
        json={
            "name": name,
            "url": url,
            "platform": "",
            "language": "auto",
            "category_id": CATEGORY_ID,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _run_worker_once(client: TestClient) -> None:
    worker = Worker(build_container(client.app.state.container.settings))
    assert worker.run_once() is True


def _db(client: TestClient) -> sqlite3.Connection:
    db_path = client.app.state.container.settings.database_path
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _value(client: TestClient, sql: str, args: tuple[object, ...] = ()) -> Any:
    connection = _db(client)
    try:
        row = connection.execute(sql, args).fetchone()
        return None if row is None else row[0]
    finally:
        connection.close()


def _write(client: TestClient, sql: str, args: tuple[object, ...] = ()) -> None:
    connection = _db(client)
    try:
        connection.execute(sql, args)
        connection.commit()
    finally:
        connection.close()


@respx.mock
def test_delete_source_cascades_and_keeps_other_sources(client: TestClient) -> None:
    """删除来源连带清理消息/版本/译文/健康/采集结果/任务，并收敛未完成运行。"""

    feed = Path("tests/fixtures/sample_feed.xml").read_bytes()
    respx.get("https://target.test/feed").mock(return_value=Response(200, content=feed))
    respx.get("https://keep.test/feed").mock(return_value=Response(200, content=feed))
    target_id = _create_source(client, "待删来源", "https://target.test/feed")
    keep_id = _create_source(client, "保留来源", "https://keep.test/feed")

    assert client.post("/api/collection/runs", json={"source_ids": [target_id]}).status_code == 202
    _run_worker_once(client)
    assert client.post("/api/collection/runs", json={"source_ids": [keep_id]}).status_code == 202
    _run_worker_once(client)

    version_id = _value(
        client,
        "SELECT id FROM message_versions WHERE message_id IN "
        "(SELECT id FROM messages WHERE source_id = ?)",
        (target_id,),
    )
    _write(
        client,
        "INSERT INTO translations(id, message_version_id, status, prompt_version, "
        "created_at, updated_at) VALUES('t-del-1', ?, 'succeeded', 'v1', 0, 0)",
        (version_id,),
    )
    assert client.post(f"/api/sources/{target_id}/test").status_code == 200
    queued_run = client.post("/api/collection/runs", json={"source_ids": [target_id]})
    assert queued_run.status_code == 202
    run_id = queued_run.json()["id"]

    assert client.get(f"/api/sources/{target_id}").json()["message_count"] == 1

    deleted = client.delete(f"/api/sources/{target_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_messages"] == 1

    assert client.get(f"/api/sources/{target_id}").status_code == 404
    assert client.delete(f"/api/sources/{target_id}").status_code == 404

    assert client.get(f"/api/sources/{keep_id}").status_code == 200
    messages = client.get("/api/messages").json()["items"]
    assert [item["source_id"] for item in messages] == [keep_id]

    assert _value(client, "SELECT COUNT(*) FROM messages WHERE source_id = ?", (target_id,)) == 0
    assert _value(client, "SELECT COUNT(*) FROM message_versions WHERE id = ?", (version_id,)) == 0
    assert _value(client, "SELECT COUNT(*) FROM translations WHERE id = 't-del-1'") == 0
    assert (
        _value(
            client, "SELECT COUNT(*) FROM source_health_checks WHERE source_id = ?", (target_id,)
        )
        == 0
    )
    assert (
        _value(client, "SELECT COUNT(*) FROM collection_results WHERE source_id = ?", (target_id,))
        == 0
    )
    assert (
        _value(
            client, "SELECT COUNT(*) FROM tasks WHERE idempotency_key LIKE ?", (f"%:{target_id}",)
        )
        == 0
    )
    assert _value(client, "SELECT COUNT(*) FROM tasks") == 1

    # 未完成运行移除该来源后收敛为成功，不再停留在排队/运行中。
    run = client.get(f"/api/collection/runs/{run_id}").json()
    assert run["source_ids"] == []
    assert run["status"] == "succeeded"
    assert run["completed_at"] is not None


def test_delete_source_clears_v1_import_map(client: TestClient) -> None:
    """导入台账按来源清理，删除后同一 v1 记录可以重新导入。"""

    source_id = _create_source(client, "导入来源", "https://legacy.test/feed")
    _write(
        client,
        "INSERT INTO v1_import_map(entity_kind, legacy_id, target_id) "
        "VALUES('source', 'legacy-1', ?)",
        (source_id,),
    )
    deleted = client.delete(f"/api/sources/{source_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_messages"] == 0
    assert (
        _value(client, "SELECT COUNT(*) FROM v1_import_map WHERE target_id = ?", (source_id,)) == 0
    )


def test_delete_unknown_source_returns_404(client: TestClient) -> None:
    response = client.delete("/api/sources/8f4c0d2e-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "source_not_found"
