"""故障、幂等、版本与密钥边界的行为验收。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from rss_v2.bootstrap import Container, build_container
from rss_v2.domain import ExternalServiceError, Provider, TaskStatus, TranslationResult
from rss_v2.tasks.worker import Worker

FEED = Path("tests/fixtures/sample_feed.xml").read_text(encoding="utf-8")


def source(client: TestClient, suffix: str = "one") -> str:
    category = client.get("/api/categories").json()[0]["id"]
    response = client.post(
        "/api/sources",
        json={"name": suffix, "url": f"https://{suffix}.test/feed", "category_id": category},
    )
    assert response.status_code == 201
    return response.json()["id"]


def provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    name: str = "primary",
    model: str = "test-model",
    refs: tuple[str, ...] = ("MAIN",),
) -> str:
    response = client.post(
        "/api/llm/providers", json={"name": name, "base_url": "https://llm.test/v1", "model": model}
    )
    assert response.status_code == 201
    provider_id = response.json()["id"]
    for priority, ref in enumerate(refs):
        assert (
            client.post(
                f"/api/llm/providers/{provider_id}/keys",
                json={"key_ref": ref, "priority": priority},
            ).status_code
            == 201
        )
        monkeypatch.setenv(f"RSS_LLM_KEY_{ref}", "sentinel-secret-do-not-persist")
    return provider_id


def collect(client: TestClient, source_id: str, worker: Worker) -> str:
    response = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert response.status_code == 202
    assert worker.run_once()
    return response.json()["id"]


class FakeProvider:
    """记录输入并按引用模拟成功、限流和超时，无真实网络。"""

    def __init__(self, failures: dict[str, str] | None = None) -> None:
        self.failures = failures or {}
        self.inputs: list[tuple[str, str]] = []

    def translate(
        self,
        provider: Provider,
        key_ref: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        self.inputs.append((key_ref, title))
        if code := self.failures.get(key_ref):
            raise ExternalServiceError(code, "模拟失败")
        return TranslationResult("中文标题", "中文摘要", "中文正文"), {"total_tokens": 3}, 1


@respx.mock
def test_versions_a_b_a_and_translation_uses_queued_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    fake = FakeProvider()
    container.translation_service.provider = fake
    worker = Worker(container)
    collect(client, source_id, worker)
    message = client.get("/api/messages").json()[0]
    provider(client, monkeypatch)
    queued = client.post(f"/api/messages/{message['id']}/translate").json()
    route.mock(
        return_value=httpx.Response(
            200, text=FEED.replace("New computing platform", "Changed computing platform")
        )
    )
    container.collection_service.collect(source_id)
    route.mock(return_value=httpx.Response(200, text=FEED))
    container.collection_service.collect(source_id)
    assert worker.run_once()
    detail = client.get(f"/api/messages/{message['id']}").json()
    assert [item["version_number"] for item in detail["versions"]] == [3, 2, 1]
    assert fake.inputs == [("MAIN", "New computing platform")]
    assert not detail["versions"][0]["translations"]
    assert (
        detail["versions"][2]["translations"][0]["message_version_id"] == queued["input_version_id"]
    )


@respx.mock
def test_url_and_content_fallback_do_not_duplicate(client: TestClient) -> None:
    route = respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    worker = Worker(client.app.state.container)
    collect(client, source_id, worker)
    route.mock(
        return_value=httpx.Response(
            200,
            text=FEED.replace("article-001", "other-guid").replace(
                "/news/1", "/news/1?utm_source=campaign"
            ),
        )
    )
    collect(client, source_id, worker)
    messages = client.get("/api/messages").json()
    assert len(messages) == 1
    assert messages[0]["latest_version"]["version_number"] == 1


@respx.mock
def test_one_failed_source_does_not_hide_success(client: TestClient) -> None:
    first, second = source(client), source(client, "two")
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    respx.get("https://two.test/feed").mock(return_value=httpx.Response(404))
    run = client.post("/api/collection/runs", json={"source_ids": [first, second]}).json()
    worker = Worker(client.app.state.container)
    assert worker.run_once()
    assert worker.run_once()
    result = client.get(f"/api/collection/runs/{run['id']}").json()
    assert result["created_count"] == 1
    assert result["failed_count"] == 1
    assert result["status"] == "running" and result["completed_at"] is None
    assert len(client.get("/api/messages").json()) == 1
    health = client.get(f"/api/sources/{second}/health").json()[0]
    assert health["http_status"] == 404
    assert health["error_code"] == "feed_http_error"


def test_lease_reclaim_fences_old_worker_and_concurrent_claim(client: TestClient) -> None:
    source_id = source(client)
    client.post("/api/collection/runs", json={"source_ids": [source_id]})
    container: Container = client.app.state.container
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: container.tasks.claim_next(["collect_source"], 100, 20), range(2))
        )
    claimed = next(item for item in results if item)
    assert sum(item is not None for item in results) == 1
    assert container.tasks.reclaim_expired(121) == 1
    resumed = container.tasks.claim_next(["collect_source"], 121, 20)
    assert resumed is not None and resumed.id == claimed.id
    assert resumed.lease_token != claimed.lease_token
    container.tasks.complete(claimed.id, lease_token=claimed.lease_token)
    assert container.tasks.get(claimed.id).status == TaskStatus.RUNNING
    assert not container.tasks.renew(claimed.id, claimed.lease_token, 130, 20)
    container.tasks.complete(resumed.id, lease_token=resumed.lease_token)
    assert container.tasks.get(claimed.id).status == TaskStatus.SUCCEEDED


@respx.mock
def test_restart_recovers_and_failed_task_can_retry(client: TestClient) -> None:
    source_id = source(client)
    run = client.post("/api/collection/runs", json={"source_ids": [source_id]}).json()
    container: Container = client.app.state.container
    expired = container.tasks.claim_next(["collect_source"], 1, 20)
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    worker = Worker(build_container(container.settings))
    assert worker.run_once()
    assert client.get(f"/api/tasks/{expired.id}").json()["status"] == "succeeded"
    assert client.post(f"/api/tasks/{expired.id}/retry").status_code == 400
    assert run["task_ids"] == [expired.id]


@pytest.mark.parametrize(
    "code", ["rate_limited", "llm_timeout", "llm_server_error", "llm_invalid_output"]
)
@respx.mock
def test_key_fallback_audit_and_no_secret_persistence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, code: str
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    provider_id = provider(client, monkeypatch, refs=("FIRST", "SECOND"))
    fake = FakeProvider({"FIRST": code})
    container.translation_service.provider = fake
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert client.post(f"/api/messages/{message_id}/translate").json()["id"] == task["id"]
    queued = client.get(f"/api/messages/{message_id}").json()["versions"][0]["translation_task"]
    assert queued["status"] == "queued" and queued["id"] == task["id"]
    assert worker.run_once()
    assert [item[0] for item in fake.inputs] == ["FIRST", "SECOND"]
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "succeeded"
    monkeypatch.delenv("RSS_LLM_KEY_FIRST")
    monkeypatch.delenv("RSS_LLM_KEY_SECOND")
    assert client.post(f"/api/messages/{message_id}/translate").json()["id"] == task["id"]
    connection = container.database.connect()
    try:
        calls = connection.execute("SELECT * FROM llm_calls").fetchall()
        assert len(calls) == 2
        assert calls[0]["input_hash"]
        dump = "\n".join(connection.iterdump())
    finally:
        connection.close()
    response = (
        client.get("/api/llm/providers").text + client.get(f"/api/messages/{message_id}").text
    )
    assert "sentinel-secret-do-not-persist" not in dump + response + caplog.text
    keys = client.get("/api/llm/providers").json()[0]["keys"]
    assert (
        keys[0]["cooldown_until"] is not None
        if code != "llm_invalid_output"
        else keys[0]["cooldown_until"] is None
    )
    assert provider_id


@respx.mock
def test_provider_fallback_and_finite_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    provider(client, monkeypatch, "primary", "model-a", ("PRIMARY",))
    fallback_id = provider(client, monkeypatch, "fallback", "model-b", ("FALLBACK",))
    container.translation_service.provider = FakeProvider({"PRIMARY": "rate_limited"})
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert worker.run_once()
    result = client.get(f"/api/messages/{message_id}").json()["versions"][0]["translations"][0]
    assert result["provider_id"] == fallback_id and result["model"] == "model-b"
    assert client.get(f"/api/tasks/{task['id']}").json()["output_version_id"] == result["id"]


@respx.mock
def test_invalid_model_output_stops_after_three_attempts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    provider(client, monkeypatch)
    container.translation_service.provider = FakeProvider({"MAIN": "llm_invalid_output"})
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    for _ in range(3):
        assert worker.run_once()
        with container.database.transaction() as connection:
            connection.execute("UPDATE tasks SET available_at=0 WHERE id=?", (task["id"],))
    assert not worker.run_once()
    failed = client.get(f"/api/tasks/{task['id']}").json()
    assert failed["status"] == "failed" and failed["attempts"] == 3
    assert client.post(f"/api/tasks/{task['id']}/retry").json()["status"] == "queued"


@pytest.mark.parametrize(
    "feed,code",
    [("<html>not a feed</html>", "feed_parse_error"), ("broken xml", "feed_parse_error")],
)
@respx.mock
def test_invalid_feed_records_failure(client: TestClient, feed: str, code: str) -> None:
    source_id = source(client)
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=feed))
    assert client.post(f"/api/sources/{source_id}/test").status_code == 400
    assert client.get(f"/api/sources/{source_id}/health").json()[0]["error_code"] == code


def test_category_source_edit_disable_and_input_boundaries(client: TestClient) -> None:
    category = client.post("/api/categories", json={"name": "自定义行业"}).json()
    created = client.post(
        "/api/sources",
        json={"name": "自定义源", "url": "https://custom.test/feed", "category_id": category["id"]},
    ).json()
    source_id = created["id"]
    assert (
        client.patch(f"/api/sources/{source_id}", json={"name": "新名称", "enabled": False}).json()[
            "enabled"
        ]
        is False
    )
    assert (
        client.get(
            "/api/sources",
            params={"enabled": "false", "q": "新名称", "category_id": category["id"]},
        ).json()[0]["id"]
        == source_id
    )
    assert client.patch(f"/api/sources/{source_id}", json={"name": None}).status_code == 422
    assert (
        client.post(
            "/api/sources",
            json={
                "name": "bad",
                "url": "https://user:secret@custom.test/feed",
                "category_id": category["id"],
            },
        ).status_code
        == 400
    )
    duplicate = client.post("/api/categories", json={"name": "重复", "slug": category["slug"]})
    assert duplicate.status_code == 409 and duplicate.json()["message"]


def test_no_auth_configuration_can_be_stored_in_provider(client: TestClient) -> None:
    for headers in ({"Authorization": "secret"}, {"x-api-key": "secret"}, {"x-thing\r\n": "abc"}):
        response = client.post(
            "/api/llm/providers",
            json={
                "name": "bad",
                "base_url": "https://llm.test/v1",
                "model": "fake",
                "extra_headers": headers,
            },
        )
        assert response.status_code == 400
    response = client.post(
        "/api/llm/providers",
        json={
            "name": "bad",
            "base_url": "https://llm.test/v1",
            "model": "fake",
            "session_header_name": "Authorization",
        },
    )
    assert response.status_code == 400
