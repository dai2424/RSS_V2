"""来源、采集和版本集成测试。"""

from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

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
            "category_id": "builtin-tech",
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

    second_run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert second_run.status_code == 202
    assert worker.run_once() is True
    messages_after = client.get("/api/messages").json()
    assert len(messages_after) == 1


def test_invalid_source_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/sources",
        json={"name": "Invalid", "url": "file:///tmp/feed", "category_id": "builtin-tech"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_url"


@respx.mock
def test_translation_uses_provider_key_and_keeps_original(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            "model": "deepseekv4.1-flash",
            "session_header_name": "x-opencode-session",
        },
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    key = client.post(f"/api/llm/providers/{provider_id}/keys", json={"key_ref": "main"})
    assert key.status_code == 201
    monkeypatch.setenv("RSS_LLM_KEY_MAIN", "secret-value")
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"新计算平台","summary":"平台已发布。","content":"新的计算平台已经发布。"}'
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
    detail = client.get(f"/api/messages/{message_id}")
    assert detail.status_code == 200
    version = detail.json()["versions"][0]
    assert version["title"] == "New computing platform"
    assert version["translations"][0]["title"] == "新计算平台"
    assert version["translations"][0]["key_ref"] == "main"
