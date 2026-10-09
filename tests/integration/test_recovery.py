"""故障、幂等、版本与密钥边界的行为验收。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from rss_v2.bootstrap import Container, build_container
from rss_v2.domain import DomainError, ExternalServiceError, Provider, TaskStatus, TranslationResult
from rss_v2.domain.values import now
from rss_v2.llm import PromptPayload
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
    name: str = "primary",
    model: str = "test-model",
    secrets: tuple[str, ...] = ("secret-main",),
) -> str:
    response = client.post(
        "/api/llm/providers", json={"name": name, "base_url": "https://llm.test/v1"}
    )
    assert response.status_code == 201
    provider_id = response.json()["id"]
    assert (
        client.post(f"/api/llm/providers/{provider_id}/models", json={"model": model}).status_code
        == 201
    )
    for priority, secret in enumerate(secrets):
        assert (
            client.post(
                f"/api/llm/providers/{provider_id}/keys",
                json={"secret": secret, "priority": priority},
            ).status_code
            == 201
        )
    return provider_id


def collect(client: TestClient, source_id: str, worker: Worker) -> str:
    response = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert response.status_code == 202
    assert worker.run_once()
    return response.json()["id"]


class FakeProvider:
    """记录输入并按密钥或模型模拟成功、限流和超时，无真实网络。"""

    def __init__(
        self, failures: dict[str, str] | None = None, model_failures: dict[str, str] | None = None
    ) -> None:
        self.failures = failures or {}
        self.model_failures = model_failures or {}
        self.inputs: list[tuple[str, str, str]] = []

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        self.inputs.append((model, secret, prompt.user))
        if code := self.model_failures.get(model):
            raise ExternalServiceError(code, "模拟失败")
        if code := self.failures.get(secret):
            raise ExternalServiceError(code, "模拟失败")
        return TranslationResult("中文标题", "中文摘要", "中文正文"), {"total_tokens": 3}, 1

    def probe(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
    ) -> tuple[str, dict[str, int], int]:
        """连接探测：只回一句话，失败注入与 translate 一致。"""

        self.inputs.append((model, secret, prompt.user))
        if code := self.model_failures.get(model):
            raise ExternalServiceError(code, "模拟失败")
        if code := self.failures.get(secret):
            raise ExternalServiceError(code, "模拟失败")
        return "The connection works.", {"total_tokens": 3}, 1


@respx.mock
def test_expired_worker_cannot_persist_translation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    provider(client)
    container.translation_service.provider = FakeProvider()
    message_id = client.get("/api/messages").json()[0]["id"]
    client.post(f"/api/messages/{message_id}/translate")
    expired = container.tasks.claim_next(["translate_message"], 100, 20)
    container.tasks.reclaim_expired(121)
    current = container.tasks.claim_next(["translate_message"], 121, 20)
    assert expired is not None and current is not None
    with pytest.raises(DomainError, match="租约"):
        container.translation_service.run(expired)
    assert not container.translations.list_for_version(expired.input_version_id)
    result = container.translation_service.run(current)
    assert result.title == "中文标题" and result.task_id == current.id


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
    provider(client)
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
    assert [item[0] for item in fake.inputs] == ["test-model"]
    assert [item[1] for item in fake.inputs] == ["secret-main"]
    assert "New computing platform" in fake.inputs[0][2]
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
            text=FEED.replace("<guid>entry-1</guid>", "<guid>other-guid</guid>").replace(
                "https://example.test/entry-1", "https://example.test/entry-1?utm_source=campaign"
            ),
        )
    )
    collect(client, source_id, worker)
    messages = client.get("/api/messages").json()
    assert len(messages) == 1
    assert messages[0]["latest_version"]["version_number"] == 1
    route.mock(
        return_value=httpx.Response(
            200,
            text=FEED.replace("<guid>entry-1</guid>", "<guid>third-guid</guid>").replace(
                "https://example.test/entry-1", "https://example.test/moved"
            ),
        )
    )
    collect(client, source_id, worker)
    assert len(client.get("/api/messages").json()) == 1
    assert client.get("/api/messages").json()[0]["latest_version"]["version_number"] == 1


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
def test_key_fallback_audit_and_masked_key_labels(
    client: TestClient, caplog: pytest.LogCaptureFixture, code: str
) -> None:
    """密钥失败切换下一枚；响应、日志与审计只出现掩码，密钥值只留在密钥表。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    secrets = ("first-key-abcdef", "second-key-fedcba")
    provider_id = provider(client, secrets=secrets)
    fake = FakeProvider({secrets[0]: code})
    container.translation_service.provider = fake
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert client.post(f"/api/messages/{message_id}/translate").json()["id"] == task["id"]
    queued = client.get(f"/api/messages/{message_id}").json()["versions"][0]["translation_task"]
    assert queued["status"] == "queued" and queued["id"] == task["id"]
    assert worker.run_once()
    assert [item[1] for item in fake.inputs] == list(secrets)
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "succeeded"
    assert client.post(f"/api/messages/{message_id}/translate").json()["id"] == task["id"]
    connection = container.database.connect()
    try:
        calls = connection.execute("SELECT * FROM llm_calls").fetchall()
        assert len(calls) == 2
        assert calls[0]["input_hash"]
        stored = {
            row["secret"] for row in connection.execute("SELECT secret FROM llm_provider_keys")
        }
    finally:
        connection.close()
    # 密钥值保存在本机密钥表；接口与日志只暴露掩码。
    assert stored == set(secrets)
    responses = (
        client.get("/api/llm/providers").text + client.get(f"/api/messages/{message_id}").text
    )
    assert not any(secret in responses or secret in caplog.text for secret in secrets)
    keys = client.get("/api/llm/providers").json()[0]["keys"]
    assert keys[0]["masked"] == "••••••••cdef"
    assert (
        keys[0]["cooldown_until"] is not None
        if code != "llm_invalid_output"
        else keys[0]["cooldown_until"] is None
    )
    assert provider_id


def test_api_key_entry_mask_replace_and_validation(client: TestClient) -> None:
    """密钥录入后只回掩码；替换与停用按行生效，空值和换行被拒绝。"""
    provider_id = provider(client, secrets=("first-key-abcdef",))
    keys = client.get("/api/llm/providers").json()[0]["keys"]
    assert keys[0]["masked"] == "••••••••cdef"
    key_id = keys[0]["id"]
    assert (
        client.post(
            f"/api/llm/providers/{provider_id}/keys", json={"secret": "has\nnewline"}
        ).status_code
        == 400
    )
    assert (
        client.post(f"/api/llm/providers/{provider_id}/keys", json={"secret": ""}).status_code
        == 422
    )
    replaced = client.patch(
        f"/api/llm/providers/{provider_id}/keys/{key_id}",
        json={"secret": "second-key-999999", "priority": 5},
    )
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["masked"] == "••••••••9999" and body["priority"] == 5
    assert "second-key-999999" not in replaced.text
    disabled = client.patch(
        f"/api/llm/providers/{provider_id}/keys/{key_id}", json={"enabled": False}
    ).json()
    assert disabled["enabled"] is False
    assert (
        client.patch(
            f"/api/llm/providers/{provider_id}/keys/{key_id}", json={"secret": " "}
        ).status_code
        == 400
    )


@respx.mock
def test_provider_fallback_and_finite_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    provider(client, "primary", "model-a", ("secret-primary",))
    fallback_id = provider(client, "fallback", "model-b", ("secret-fallback",))
    container.translation_service.provider = FakeProvider({"secret-primary": "rate_limited"})
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert worker.run_once()
    result = client.get(f"/api/messages/{message_id}").json()["versions"][0]["translations"][0]
    assert result["provider_id"] == fallback_id and result["model"] == "model-b"
    assert client.get(f"/api/tasks/{task['id']}").json()["output_version_id"] == result["id"]


def test_connection_test_success_cools_down_failures_and_audits(
    client: TestClient,
) -> None:
    """连接测试复用真实候选规则：成功返回延迟与掩码，失败切换下一枚并写审计。"""
    container: Container = client.app.state.container
    missing = client.post("/api/llm/providers/absent/test", json={})
    assert missing.status_code == 404
    provider_id = provider(client, secrets=("probe-key-111111", "probe-key-222222"))
    container.translation_service.provider = FakeProvider({"probe-key-111111": "rate_limited"})
    response = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["model"] == "test-model"
    assert body["key_masked"] == "••••••••2222" and body["latency_ms"] >= 0
    assert "probe-key-222222" not in response.text
    connection = container.database.connect()
    try:
        calls = connection.execute(
            "SELECT task_id, key_masked, status, prompt_version FROM llm_calls ORDER BY created_at"
        ).fetchall()
    finally:
        connection.close()
    assert [(row["task_id"], row["status"]) for row in calls] == [
        (None, "failed"),
        (None, "succeeded"),
    ]
    masks = {row["key_masked"] for row in calls}
    assert masks == {"••••••••1111", "••••••••2222"}
    assert calls[0]["prompt_version"].endswith("+connection-test")
    keys = next(
        item for item in client.get("/api/llm/providers").json() if item["id"] == provider_id
    )["keys"]
    assert keys[0]["cooldown_until"] is not None
    assert keys[1]["last_status"] == "succeeded"


def test_connection_test_requires_model_and_usable_key(client: TestClient) -> None:
    """没有启用模型或没有可用 Key 时直接给出可读错误，不发起调用。"""
    provider_id = provider(client, "empty-models")
    models = client.get("/api/llm/providers").json()[0]["models"]
    client.patch(
        f"/api/llm/providers/{provider_id}/models/{models[0]['id']}", json={"enabled": False}
    )
    no_model = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert no_model.status_code == 400 and no_model.json()["code"] == "model_not_configured"
    keys = client.get("/api/llm/providers").json()[0]["keys"]
    client.patch(f"/api/llm/providers/{provider_id}/keys/{keys[0]['id']}", json={"enabled": False})
    client.patch(
        f"/api/llm/providers/{provider_id}/models/{models[0]['id']}", json={"enabled": True}
    )
    no_key = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert no_key.status_code == 400 and no_key.json()["code"] == "llm_key_not_configured"


def models_of(client: TestClient, provider_id: str) -> list[str]:
    """读取指定 provider 的模型列表并返回模型 ID 顺序。"""

    listing = next(
        item for item in client.get("/api/llm/providers").json() if item["id"] == provider_id
    )
    return [item["model"] for item in listing["models"]]


def test_provider_models_crud_and_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_id = provider(client, model="model-a")
    duplicate = client.post(f"/api/llm/providers/{provider_id}/models", json={"model": "model-a"})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "model_exists"
    assert client.post("/api/llm/providers/absent/models", json={"model": "x"}).status_code == 404
    second = client.post(
        f"/api/llm/providers/{provider_id}/models", json={"model": "model-b", "priority": 20}
    ).json()
    assert models_of(client, provider_id) == ["model-b", "model-a"]
    disabled = client.patch(
        f"/api/llm/providers/{provider_id}/models/{second['id']}", json={"enabled": False}
    )
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    renamed = client.patch(
        f"/api/llm/providers/{provider_id}/models/{second['id']}",
        json={"model": "model-c", "priority": 5},
    ).json()
    assert renamed["model"] == "model-c" and renamed["priority"] == 5
    assert (
        client.patch(f"/api/llm/providers/{provider_id}/models/{second['id']}", json={"model": ""})
    ).status_code == 422
    assert (
        client.patch(
            f"/api/llm/providers/{provider_id}/models/{second['id']}", json={"model": "model-a"}
        )
    ).status_code == 409
    other_id = provider(client, name="other", secrets=("secret-other",))
    assert (
        client.patch(f"/api/llm/providers/{other_id}/models/{second['id']}", json={"enabled": True})
    ).status_code == 404
    assert models_of(client, provider_id) == ["model-c", "model-a"]
    assert models_of(client, other_id) == ["test-model"]


@respx.mock
def test_model_fallback_within_provider_keeps_key_accountable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同 Provider 下模型失败切换到下一个模型；非冷却错误不拖累 Key。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    provider_id = provider(client, secrets=("secret-primary",))
    client.post(
        f"/api/llm/providers/{provider_id}/models", json={"model": "model-a", "priority": 10}
    )
    client.post(
        f"/api/llm/providers/{provider_id}/models", json={"model": "model-b", "priority": 20}
    )
    container.translation_service.provider = FakeProvider(
        model_failures={"model-a": "llm_invalid_output"}
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert worker.run_once()
    result = client.get(f"/api/messages/{message_id}").json()["versions"][0]["translations"][0]
    assert result["model"] == "model-b" and result["provider_id"] == provider_id
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "succeeded"
    connection = container.database.connect()
    try:
        calls = connection.execute(
            "SELECT model, status FROM llm_calls ORDER BY created_at"
        ).fetchall()
        assert [(row["model"], row["status"]) for row in calls] == [
            ("model-a", "failed"),
            ("model-b", "succeeded"),
        ]
    finally:
        connection.close()


@respx.mock
def test_invalid_model_output_stops_after_three_attempts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    worker = Worker(container)
    collect(client, source_id, worker)
    provider(client)
    container.translation_service.provider = FakeProvider({"secret-main": "llm_invalid_output"})
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


@respx.mock
def test_model_retry_waits_for_configured_cooldown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    container.settings.llm_retry_cooldown_seconds = 120
    container.translation_service.cooldown_seconds = 120
    collect(client, source_id, Worker(container))
    provider(client)
    container.translation_service.provider = FakeProvider({"secret-main": "rate_limited"})
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert Worker(container).run_once()
    queued = container.tasks.get(task["id"])
    assert queued.status == TaskStatus.QUEUED
    assert queued.available_at >= now() + 119
    assert not Worker(container).run_once()


@pytest.mark.parametrize(
    "content", ["not json", '{"title":"标题"}', '{"title":4,"summary":"摘要","content":"正文"}']
)
@respx.mock
def test_compatible_adapter_rejects_malformed_translation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    provider(client)
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert Worker(container).run_once()
    status = client.get(f"/api/tasks/{task['id']}").json()
    assert status["error_code"] == "llm_invalid_output"
    version = client.get(f"/api/messages/{message_id}").json()["versions"][0]
    assert version["title"] == "New computing platform"
    assert version["translations"][0]["status"] == "failed"


@pytest.mark.parametrize(
    ("content", "expected_keywords"),
    [
        # 推理模型和部分网关会给 JSON 加 Markdown 围栏或前后说明文字。
        (
            '```json\n{"title":"精简标题","summary":"摘要","keywords":["甲","乙"]}\n```',
            ["甲", "乙"],
        ),
        (
            '好的：\n{"title":"精简标题","summary":"摘要","keywords":["甲","乙"]}\n希望有帮助。',
            ["甲", "乙"],
        ),
        # 关键词偶尔被写成顿号或逗号分隔的字符串。
        ('{"title":"精简标题","summary":"摘要","keywords":"甲, 乙、丙"}', ["甲", "乙", "丙"]),
    ],
)
@respx.mock
def test_compatible_adapter_accepts_recoverable_enrichment_shapes(
    client: TestClient, content: str, expected_keywords: list[str]
) -> None:
    """可恢复的输出形状不应让加工任务失败。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    provider(client)
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        )
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/enrich").json()
    assert Worker(container).run_once()
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "succeeded"
    enrichment = client.get(f"/api/messages/{message_id}").json()["versions"][0]["enrichments"][0]
    assert enrichment["title"] == "精简标题"
    assert enrichment["keywords"] == expected_keywords


@pytest.mark.parametrize(
    ("content", "finish_reason", "fragments"),
    [
        # 字段类型不符：错误信息要指出具体字段。
        (
            '{"title":4,"summary":"摘要","keywords":[]}',
            "stop",
            ("title（string_type）", "返回片段"),
        ),
        # 被输出上限截断：错误信息要带上结束原因和残缺片段。
        (
            '{"title":"精简标题","summary":"摘要","keywords":["甲"',
            "length",
            ("json_invalid", "结束原因 length", "精简标题"),
        ),
    ],
)
@respx.mock
def test_invalid_output_reports_field_and_snippet(
    client: TestClient, content: str, finish_reason: str, fragments: tuple[str, ...]
) -> None:
    """结构校验失败必须能定位：给出失败字段、结束原因和返回片段。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    provider(client)
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]},
        )
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/enrich").json()
    assert Worker(container).run_once()
    status = client.get(f"/api/tasks/{task['id']}").json()
    assert status["error_code"] == "llm_invalid_output"
    assert status["error_message"].startswith("模型返回的结构无法校验：")
    for fragment in fragments:
        assert fragment in status["error_message"]


@respx.mock
def test_invalid_output_never_leaks_api_key(client: TestClient) -> None:
    """上游把请求内容回显进返回片段时，错误信息仍不能出现密钥值。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    provider(client, secrets=("secret-main",))
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"title":4,"summary":"secret-main 回显","keywords":[]}'
                        }
                    }
                ]
            },
        )
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/enrich").json()
    assert Worker(container).run_once()
    message = client.get(f"/api/tasks/{task['id']}").json()["error_message"]
    assert "secret-main" not in message and "***" in message


@respx.mock
def test_gateway_rejecting_optional_params_falls_back_to_minimal_payload(
    client: TestClient,
) -> None:
    """兼容网关拒绝 response_format/temperature 时去掉可选参数重试一次。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    provider(client, secrets=("secret-strict",))
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "response_format is not supported"}}),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"title": "新计算平台", "summary": "摘要", "content": "正文"}
                                )
                            }
                        }
                    ],
                    "usage": {"total_tokens": 5},
                },
            ),
        ]
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert Worker(container).run_once()
    assert route.call_count == 2
    retried = json.loads(route.calls[1].request.content)
    assert retried["model"] == "test-model"
    assert "response_format" not in retried and "temperature" not in retried
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "succeeded"


@respx.mock
def test_gateway_error_detail_is_surfaced_without_key(client: TestClient) -> None:
    """上游错误说明进入错误信息便于排查；密钥值即使被上游回显也会被替换。"""
    respx.get("https://one.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    source_id = source(client)
    container: Container = client.app.state.container
    collect(client, source_id, Worker(container))
    secret = "super-secret-key-xyz"
    provider(client, secrets=(secret,))
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": f"Invalid API key: {secret}"}})
    )
    message_id = client.get("/api/messages").json()[0]["id"]
    task = client.post(f"/api/messages/{message_id}/translate").json()
    assert Worker(container).run_once()
    described = client.get(f"/api/tasks/{task['id']}").json()
    assert described["error_code"] == "llm_auth_error"
    assert "Invalid API key" in described["error_message"]
    assert secret not in described["error_message"]
    failed = client.get(f"/api/messages/{message_id}").json()["versions"][0]["translations"][0]
    assert failed["error_code"] == "llm_auth_error"
    assert secret not in (failed["error_message"] or "")


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
    filtered = client.get(
        "/api/sources",
        params={"enabled": "false", "q": "新名称", "category_id": category["id"]},
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == source_id
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
                "extra_headers": headers,
            },
        )
        assert response.status_code == 400
    response = client.post(
        "/api/llm/providers",
        json={
            "name": "bad",
            "base_url": "https://llm.test/v1",
            "session_header_name": "Authorization",
        },
    )
    assert response.status_code == 400
