"""任务删除与清理失败任务的集成测试：状态守卫、结果与审计保留。"""

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import (
    KeywordProvider,
    collect,
    configure_model,
    create_source,
    drain_worker,
    feed,
    stub_providers,
)

from rss_v2.bootstrap import Container
from rss_v2.domain import Keyword, KeywordKind

FEED = [("only", "唯一的一条", "Thu, 08 Oct 2026 00:00:00 GMT")]

#: 直接造任务行用的 SQL；只填必需列，用来构造排队与失败状态。
INSERT_TASK = (
    "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,payload_json,"
    "created_at,updated_at,available_at) VALUES(?,?,?,?,?,?,?,?,?)"
)


def seeded(client: TestClient, url: str = "https://tasks.test/feed") -> None:
    """造一条已加工的消息，让库里有一个成功任务。"""

    respx.get(url).mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    stub_providers(client, KeywordProvider(default=(Keyword("计算平台", KeywordKind.ENTITY),)))
    source_id = create_source(client, url)
    collect(client, source_id)
    drain_worker(client)


def insert_task(
    client: TestClient, task_id: str, status: str, task_type: str = "enrich_message"
) -> None:
    container: Container = client.app.state.container
    with container.database.transaction() as connection:
        connection.execute(
            INSERT_TASK,
            (task_id, task_type, f"key-{task_id}", status, 1, "{}", 1, 1, 0),
        )


def scalar(client: TestClient, sql: str) -> int:
    container: Container = client.app.state.container
    connection = container.database.connect()
    try:
        return int(connection.execute(sql).fetchone()[0])
    finally:
        connection.close()


@respx.mock
def test_delete_finished_task_keeps_results_and_audit(client: TestClient) -> None:
    """删除已结束的任务：任务行消失，加工结果与模型调用审计保留。"""

    seeded(client)
    # 采集任务也会成功，这里只看内容加工任务。
    finished = [
        item
        for item in client.get("/api/tasks", params={"status": "succeeded"}).json()["items"]
        if item["task_type"] == "enrich_message"
    ]
    assert len(finished) == 1
    task_id = str(finished[0]["id"])
    calls = scalar(client, "SELECT COUNT(*) FROM llm_calls")

    deleted = client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"candidates": 1, "deleted": 1}
    assert client.get(f"/api/tasks/{task_id}").status_code == 404
    # 结果表与调用审计都不受影响。
    assert scalar(client, "SELECT COUNT(*) FROM message_enrichments") == 1
    assert scalar(client, "SELECT COUNT(*) FROM llm_calls") == calls
    # 结果行里的 task_id 变成历史引用，不影响读取。
    message_id = client.get("/api/messages").json()["items"][0]["id"]
    version = client.get(f"/api/messages/{message_id}").json()["versions"][0]
    assert version["enrichments"][0]["status"] == "succeeded"


@respx.mock
def test_unfinished_task_cannot_be_deleted(client: TestClient) -> None:
    """排队中与运行中的任务拒绝删除；错误信息说明原因而不是静默跳过。"""

    seeded(client)
    insert_task(client, "queued-task", "queued")
    insert_task(client, "running-task", "running")

    for task_id in ("queued-task", "running-task"):
        blocked = client.delete(f"/api/tasks/{task_id}")
        assert blocked.status_code == 400, blocked.text
        assert blocked.json()["code"] == "task_not_finished"
        assert scalar(client, f"SELECT COUNT(*) FROM tasks WHERE id='{task_id}'") == 1


@respx.mock
def test_clear_failed_previews_then_deletes(client: TestClient) -> None:
    """清理失败任务：先预览条数，再删除；成功任务与结果不动。"""

    seeded(client)
    insert_task(client, "failed-a", "failed")
    insert_task(client, "failed-b", "failed", "translate_message")

    preview = client.post("/api/tasks/clear-failed", json={"dry_run": True})
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"candidates": 2, "deleted": 0}
    assert scalar(client, "SELECT COUNT(*) FROM tasks WHERE status='failed'") == 2

    executed = client.post("/api/tasks/clear-failed", json={})
    assert executed.json() == {"candidates": 2, "deleted": 2}
    assert scalar(client, "SELECT COUNT(*) FROM tasks WHERE status='failed'") == 0
    # 已成功的任务与结果不受影响。
    assert (
        scalar(
            client,
            "SELECT COUNT(*) FROM tasks WHERE status='succeeded' AND task_type='enrich_message'",
        )
        == 1
    )
    assert scalar(client, "SELECT COUNT(*) FROM message_enrichments") == 1


@respx.mock
def test_delete_unknown_task_reports_not_found(client: TestClient) -> None:
    """未知任务 404。"""

    seeded(client)
    assert client.delete("/api/tasks/no-such-task").status_code == 404
