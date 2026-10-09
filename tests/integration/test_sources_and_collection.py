"""来源、采集和版本集成测试。"""

import json
from pathlib import Path

import respx
from fastapi.testclient import TestClient
from httpx import Response

from rss_v2.adapters.llm.boundary import stable_session_id
from rss_v2.bootstrap import build_container
from rss_v2.tasks.worker import Worker


def _create_source(client: TestClient) -> str:
    response = client.post(
        "/api/sources",
        json={
            "name": "Example Tech",
            "url": "https://example.test/feed.xml",
            "platform": "Example",
            "language": "auto",
            "category_id": "8cabd1f6-c0c3-4b32-863d-3836cf5a8171",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@respx.mock
def test_source_create_test_and_collection_are_idempotent(client: TestClient) -> None:
    feed = Path("tests/fixtures/sample_feed.xml").read_bytes()
    route = respx.get("https://example.test/feed.xml").mock(
        return_value=Response(200, content=feed)
    )
    source_id = _create_source(client)

    tested = client.post(f"/api/sources/{source_id}/test")
    assert tested.status_code == 200
    assert tested.json()["health"]["parse_success"] is True
    assert route.called

    run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert run.status_code == 202
    assert len(run.json()["task_ids"]) == 1

    settings = client.app.state.container.settings
    worker = Worker(build_container(settings))
    assert worker.run_once() is True
    messages = client.get("/api/messages").json()
    assert len(messages) == 1
    assert messages[0]["latest_version"]["title"] == "New computing platform"
    # 来源只有 description 时正文留空，界面不会把同一段文字展示两次。
    assert messages[0]["latest_version"]["summary"] == "A new computing platform is available."
    assert messages[0]["latest_version"]["content"] == ""

    second_run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert second_run.status_code == 202
    assert worker.run_once() is True
    messages_after = client.get("/api/messages").json()
    assert len(messages_after) == 1


def test_invalid_source_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/sources",
        json={
            "name": "Invalid",
            "url": "file:///tmp/feed",
            "category_id": "8cabd1f6-c0c3-4b32-863d-3836cf5a8171",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_url"


def test_source_order_is_stable_across_edits(client: TestClient) -> None:
    """列表按创建时间倒序；编辑和启停不改变行位置，同一秒内按入库顺序决胜。"""
    category_id = "8cabd1f6-c0c3-4b32-863d-3836cf5a8171"
    first = client.post(
        "/api/sources",
        json={"name": "来源一", "url": "https://one.test/feed", "category_id": category_id},
    ).json()
    second = client.post(
        "/api/sources",
        json={"name": "来源二", "url": "https://two.test/feed", "category_id": category_id},
    ).json()
    assert client.patch(f"/api/sources/{first['id']}", json={"enabled": False}).status_code == 200
    listed = client.get("/api/sources").json()
    assert [item["id"] for item in listed["items"]] == [second["id"], first["id"]]


@respx.mock
def test_translation_uses_provider_key_and_keeps_original(client: TestClient) -> None:
    feed = Path("tests/fixtures/sample_feed.xml").read_bytes()
    respx.get("https://example.test/feed.xml").mock(return_value=Response(200, content=feed))
    source_id = _create_source(client)
    run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert run.status_code == 202
    settings = client.app.state.container.settings
    worker = Worker(build_container(settings))
    assert worker.run_once() is True
    message_id = client.get("/api/messages").json()[0]["id"]

    provider = client.post(
        "/api/llm/providers",
        json={
            "name": "OpenCode",
            "base_url": "https://llm.test/v1",
            "session_header_name": "x-opencode-session",
        },
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    model = client.post(
        f"/api/llm/providers/{provider_id}/models", json={"model": "deepseekv4.1-flash"}
    )
    assert model.status_code == 201, model.text
    key = client.post(f"/api/llm/providers/{provider_id}/keys", json={"secret": "live-key-123456"})
    assert key.status_code == 201
    proxy = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "新计算平台",
                                    "summary": "平台已发布。",
                                    "content": "新的计算平台已经发布。",
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )
    )
    translation_task = client.post(f"/api/messages/{message_id}/translate")
    assert translation_task.status_code == 202, translation_task.text
    assert worker.run_once() is True
    # 会话头在会话内稳定、客户端标识自身，网关据此做路由与缓存亲和。
    first_headers = proxy.calls[0].request.headers
    assert first_headers["x-opencode-session"] == stable_session_id(provider_id)
    assert first_headers["user-agent"] == "rss-v2/0.1"
    # 连接测试是同一 Provider 的第二次真实调用，会话头必须保持不变。
    assert client.post(f"/api/llm/providers/{provider_id}/test", json={}).status_code == 200
    assert (
        proxy.calls[1].request.headers["x-opencode-session"] == first_headers["x-opencode-session"]
    )
    detail = client.get(f"/api/messages/{message_id}")
    assert detail.status_code == 200
    version = detail.json()["versions"][0]
    assert version["title"] == "New computing platform"
    assert version["translations"][0]["title"] == "新计算平台"
    assert version["translations"][0]["key_masked"] == "•" * 8 + "3456"
