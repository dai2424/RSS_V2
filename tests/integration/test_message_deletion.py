"""消息删除的集成测试：级联范围、影响面预览、审计保留与批量上限。"""

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

#: 英文长文：翻译与内容加工都会入队，删除时两类任务都要被清理。
ENGLISH_TEXT = "A long English description that triggers both translation and enrichment. " * 8

FEED = [
    ("keep", "保留的那条", "Thu, 08 Oct 2026 00:00:00 GMT"),
    ("drop", "要删的那条", "Wed, 07 Oct 2026 09:00:00 GMT"),
]


def provider() -> KeywordProvider:
    return KeywordProvider(
        default=(Keyword("计算平台", KeywordKind.ENTITY), Keyword("发布", KeywordKind.EVENT))
    )


def seeded(client: TestClient, url: str = "https://delete.test/feed") -> list[dict[str, object]]:
    """造一个来源两条英文消息，并把翻译与加工都跑完。"""

    respx.get(url).mock(return_value=Response(200, content=feed(FEED, ENGLISH_TEXT)))
    configure_model(client)
    stub_providers(client, provider())
    source_id = create_source(client, url)
    collect(client, source_id)
    drain_worker(client)
    return list(client.get("/api/messages").json()["items"])


def counts(client: TestClient, message_id: str) -> dict[str, int]:
    """按被删消息统计各表的行数，用来断言级联是否干净。"""

    container: Container = client.app.state.container
    connection = container.database.connect()
    try:
        version_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM message_versions WHERE message_id = ?", (message_id,)
            )
        ]
        placeholders = ",".join("?" for _ in version_ids) or "''"
        table_counts = {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE message_version_id IN ({placeholders})",
                    tuple(version_ids),
                ).fetchone()[0]
            )
            for table in ("translations", "message_enrichments", "enrichment_keywords")
        }
        table_counts["versions"] = len(version_ids)
        table_counts["tasks"] = int(
            connection.execute(
                f"SELECT COUNT(*) FROM tasks WHERE input_version_id IN ({placeholders})",
                tuple(version_ids),
            ).fetchone()[0]
        )
        return table_counts
    finally:
        connection.close()


def scalar(client: TestClient, sql: str, args: tuple[object, ...] = ()) -> int:
    container: Container = client.app.state.container
    connection = container.database.connect()
    try:
        return int(connection.execute(sql, args).fetchone()[0])
    finally:
        connection.close()


def target(messages: list[dict[str, object]], title: str) -> dict[str, object]:
    return next(item for item in messages if item["latest_version"]["title"] == title)  # type: ignore[index]


@respx.mock
def test_delete_message_cleans_cascade_and_keeps_audit(client: TestClient) -> None:
    """删除单条消息：版本、译文、加工结果、关键词与任务都清掉，调用审计保留。"""

    messages = seeded(client)
    dropped = target(messages, "要删的那条")
    kept = target(messages, "保留的那条")
    message_id = str(dropped["id"])

    before = counts(client, message_id)
    assert before == {
        "versions": 1,
        "translations": 1,
        "message_enrichments": 1,
        "enrichment_keywords": 2,
        "tasks": 2,
    }
    calls = scalar(client, "SELECT COUNT(*) FROM llm_calls")

    preview = client.get(f"/api/messages/{message_id}/impact")
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "messages": 1,
        "versions": 1,
        "translations": 1,
        "enrichments": 1,
        "tasks": 2,
    }
    # 预览不改数据。
    assert counts(client, message_id) == before

    deleted = client.delete(f"/api/messages/{message_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == preview.json()
    assert counts(client, message_id) == {
        "versions": 0,
        "translations": 0,
        "message_enrichments": 0,
        "enrichment_keywords": 0,
        "tasks": 0,
    }
    # 模型调用审计保留：删消息不该抹掉"调用发生过"的事实。
    assert scalar(client, "SELECT COUNT(*) FROM llm_calls") == calls
    # 另一条消息完好。
    assert counts(client, str(kept["id"]))["versions"] == 1
    remaining = client.get("/api/messages").json()["items"]
    assert [item["latest_version"]["title"] for item in remaining] == ["保留的那条"]


@respx.mock
def test_delete_message_clears_v1_import_mapping(client: TestClient) -> None:
    """v1 导入台账同步清理：否则重新导入时这些记录会被跳过。"""

    messages = seeded(client)
    message_id = str(target(messages, "要删的那条")["id"])
    container: Container = client.app.state.container
    version_id = str(target(messages, "要删的那条")["latest_version"]["id"])  # type: ignore[index]
    with container.database.transaction() as connection:
        # 台账按 (entity_kind, legacy_id) 唯一，target_id 指向 v2 实体。
        connection.executemany(
            "INSERT INTO v1_import_map(entity_kind, legacy_id, target_id) VALUES(?,?,?)",
            [
                ("message", "legacy-1", message_id),
                ("version", "legacy-1-v1", version_id),
                ("message", "legacy-2", "other-message"),
            ],
        )

    assert client.delete(f"/api/messages/{message_id}").status_code == 200
    rows = scalar(client, "SELECT COUNT(*) FROM v1_import_map")
    assert rows == 1
    assert (
        scalar(client, "SELECT COUNT(*) FROM v1_import_map WHERE target_id = ?", ("other-message",))
        == 1
    )


@respx.mock
def test_bulk_delete_previews_then_deletes_and_rejects_too_many(client: TestClient) -> None:
    """批量删除：dry_run 只预览；超过 200 条被拒绝。"""

    messages = seeded(client)
    ids = [str(item["id"]) for item in messages]

    preview = client.post("/api/messages/bulk-delete", json={"message_ids": ids, "dry_run": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["messages"] == 2
    assert client.get("/api/messages").json()["total"] == 2

    executed = client.post("/api/messages/bulk-delete", json={"message_ids": ids})
    assert executed.json()["messages"] == 2
    assert executed.json()["tasks"] == 4
    assert client.get("/api/messages").json()["total"] == 0

    too_many = client.post(
        "/api/messages/bulk-delete", json={"message_ids": [f"id-{index}" for index in range(201)]}
    )
    assert too_many.status_code == 422, too_many.text


@respx.mock
def test_unknown_message_reports_not_found(client: TestClient) -> None:
    """未知 id 明确 404，而不是静默成功。"""

    seeded(client)
    assert client.get("/api/messages/no-such-message/impact").status_code == 404
    assert client.delete("/api/messages/no-such-message").status_code == 404
    bulk = client.post("/api/messages/bulk-delete", json={"message_ids": ["no-such-message"]})
    assert bulk.status_code == 404, bulk.text
