"""Provider 协议选择与 Anthropic Messages 协议的行为验收。"""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from rss_v2.adapters.llm.anthropic_messages import AnthropicMessagesProvider
from rss_v2.adapters.llm.router import ProtocolRouter
from rss_v2.bootstrap import Container
from rss_v2.domain import (
    EnrichmentResult,
    ExternalServiceError,
    LLMProtocol,
    Provider,
    TranslationResult,
)
from rss_v2.llm import PromptPayload
from rss_v2.tasks.worker import Worker

FEED = Path("tests/fixtures/sample_feed.xml").read_text(encoding="utf-8")


def api_provider(
    client: TestClient,
    name: str = "messages",
    protocol: str = "anthropic_messages",
    secrets: tuple[str, ...] = ("secret-main",),
) -> str:
    """建一个指定协议的 provider，含一个启用模型与若干 Key。"""

    response = client.post(
        "/api/llm/providers",
        json={"name": name, "base_url": "https://llm.test/v1", "protocol": protocol},
    )
    assert response.status_code == 201, response.text
    provider_id = str(response.json()["id"])
    assert response.json()["protocol"] == protocol
    assert (
        client.post(
            f"/api/llm/providers/{provider_id}/models", json={"model": "test-model"}
        ).status_code
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


def source(client: TestClient, suffix: str = "messages") -> str:
    category = client.get("/api/categories").json()[0]["id"]
    response = client.post(
        "/api/sources",
        json={"name": suffix, "url": f"https://{suffix}.test/feed", "category_id": category},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def collect(client: TestClient, source_id: str, worker: Worker) -> None:
    response = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert response.status_code == 202, response.text
    assert worker.run_once()


def messages_reply(content: dict[str, object], *, stop_reason: str = "end_turn") -> httpx.Response:
    """Anthropic Messages 形状的成功响应。"""

    return httpx.Response(
        200,
        json={
            "content": [{"type": "text", "text": json.dumps(content, ensure_ascii=False)}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 11, "output_tokens": 7},
        },
    )


def test_provider_protocol_defaults_and_rejects_unknown(client: TestClient) -> None:
    """协议默认 OpenAI 兼容；未实现的取值在写入前就被拒绝。"""

    created = client.post(
        "/api/llm/providers", json={"name": "default", "base_url": "https://llm.test/v1"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["protocol"] == "chat_completions"
    provider_id = str(created.json()["id"])

    switched = client.patch(
        f"/api/llm/providers/{provider_id}", json={"protocol": "anthropic_messages"}
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["protocol"] == "anthropic_messages"
    listed = next(
        item for item in client.get("/api/llm/providers").json() if item["id"] == provider_id
    )
    assert listed["protocol"] == "anthropic_messages"

    rejected = client.patch(f"/api/llm/providers/{provider_id}", json={"protocol": "messages"})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "invalid_protocol"
    assert "anthropic_messages" in rejected.json()["message"]
    bad_create = client.post(
        "/api/llm/providers",
        json={"name": "bad", "base_url": "https://llm.test/v1", "protocol": "messages"},
    )
    assert bad_create.status_code == 400
    assert bad_create.json()["code"] == "invalid_protocol"


@respx.mock
def test_anthropic_messages_translation_uses_messages_shape(client: TestClient) -> None:
    """按 /messages 形状发请求：系统提示在顶层、认证用 x-api-key、输出上限必填。"""

    respx.get("https://messages.test/feed").mock(return_value=httpx.Response(200, text=FEED))
    route = respx.post("https://llm.test/v1/messages").mock(
        return_value=messages_reply(
            {"title": "中文标题", "summary": "中文摘要", "content": "中文正文"}
        )
    )
    container: Container = client.app.state.container
    worker = Worker(container)
    api_provider(client)
    source_id = source(client)
    collect(client, source_id, worker)
    message_id = str(client.get("/api/messages").json()[0]["id"])
    task = client.post(f"/api/messages/{message_id}/translate")
    assert task.status_code in {200, 202}, task.text
    assert worker.run_once() is True

    request = route.calls[0].request
    body = json.loads(request.content)
    assert str(request.url).endswith("/v1/messages")
    assert body["model"] == "test-model"
    assert body["max_tokens"] >= 1
    assert [item["role"] for item in body["messages"]] == ["user"]
    assert "[task: translate_message," in body["system"]
    assert request.headers["x-api-key"] == "secret-main"
    assert request.headers["anthropic-version"] == "2023-06-01"

    version = client.get(f"/api/messages/{message_id}").json()["versions"][0]
    assert version["translations"][0]["title"] == "中文标题"
    assert version["translations"][0]["status"] == "succeeded"
    # 用量折算成与 chat completions 一致的字段，审计表里两种协议可以直接比较。
    connection = container.database.connect()
    try:
        row = connection.execute(
            "SELECT token_usage_json, prompt_version FROM llm_calls ORDER BY created_at DESC"
        ).fetchone()
    finally:
        connection.close()
    assert json.loads(row["token_usage_json"]) == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert row["prompt_version"].startswith("translation-v")


@respx.mock
def test_anthropic_messages_enrichment_and_error_mapping(client: TestClient) -> None:
    """加工任务同样走 Messages 协议；上游 429 复用同一套错误分类与冷却。"""

    def reply_for(request: httpx.Request) -> httpx.Response:
        """按请求里的任务标记返回对应结构，和真实上游一样区分两条链路。"""

        body = json.loads(request.content)
        if "[task: enrich_message" in str(body.get("system", "")):
            return messages_reply(
                {"title": "精简标题", "summary": "摘要", "keywords": "关键词、发布"}
            )
        return messages_reply({"title": "中文标题", "summary": "中文摘要", "content": "中文正文"})

    route = respx.post("https://llm.test/v1/messages").mock(side_effect=reply_for)
    container: Container = client.app.state.container
    provider_id = api_provider(client)
    tested = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True
    assert tested.json()["total_tokens"] == 18
    assert route.calls[0].request.headers["x-api-key"] == "secret-main"

    # 加工链路复用同一协议边界：关键词字符串按分隔符折算成列表。
    adapter = AnthropicMessagesProvider()
    provider = container.llm_config.get_provider(provider_id)
    assert provider is not None
    result, tokens, _ = adapter.enrich(
        provider, "test-model", "secret-main", PromptPayload("系统", "用户"), "enrich-v1"
    )
    assert [item.text for item in result.keywords] == ["关键词", "发布"]
    assert tokens["total_tokens"] == 18

    route.mock(return_value=httpx.Response(429, json={"error": {"message": "slow down"}}))
    limited = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert limited.status_code == 200
    assert limited.json()["ok"] is False
    assert limited.json()["error_code"] == "rate_limited"
    keys = next(
        item for item in client.get("/api/llm/providers").json() if item["id"] == provider_id
    )["keys"]
    assert keys[0]["cooldown_until"] is not None


def test_protocol_router_dispatches_and_rejects_unknown() -> None:
    """按 Provider 的协议取值分发；未注册协议明确报错，不回退默认实现。"""

    class Recording:
        """记录被调用的协议方法，替代真实网络调用。"""

        def __init__(self) -> None:
            self.calls: list[str] = []

        def translate(
            self,
            provider: Provider,
            model: str,
            secret: str,
            prompt: PromptPayload,
            prompt_version: str,
        ) -> tuple[TranslationResult, dict[str, int], int]:
            self.calls.append("translate")
            return TranslationResult("标题", "摘要", "正文"), {}, 0

        def enrich(
            self,
            provider: Provider,
            model: str,
            secret: str,
            prompt: PromptPayload,
            prompt_version: str,
        ) -> tuple[EnrichmentResult, dict[str, int], int]:
            self.calls.append("enrich")
            return EnrichmentResult("标题", "摘要", ()), {}, 0

    chat = Recording()
    router = ProtocolRouter({LLMProtocol.CHAT_COMPLETIONS.value: chat})
    provider = Provider(
        "provider-id",
        "名称",
        "https://llm.test/v1",
        LLMProtocol.CHAT_COMPLETIONS.value,
        True,
        1.0,
        None,
        0,
        0,
    )
    payload = PromptPayload("系统", "用户")
    router.translate(provider, "model", "secret", payload, "translation-v1")
    router.enrich(provider, "model", "secret", payload, "enrich-v1")
    assert chat.calls == ["translate", "enrich"]

    unregistered = replace(provider, protocol="vertex_ai")
    with pytest.raises(ExternalServiceError) as excinfo:
        router.translate(unregistered, "model", "secret", payload, "translation-v1")
    assert excinfo.value.code == "llm_protocol_unsupported"


@respx.mock
def test_probe_accepts_plain_text_reply_over_messages(client: TestClient) -> None:
    """连接测试只要求上游按协议回话：模型回一句话就算连通（线上故障的回归）。"""

    route = respx.post("https://llm.test/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "The connection works."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 9, "output_tokens": 4},
            },
        )
    )
    provider_id = api_provider(client)
    tested = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] is True
    assert body["reply"] == "The connection works."
    assert body["total_tokens"] == 13

    sent = json.loads(route.calls[0].request.content)
    assert "[task: probe," in sent["system"]
    assert sent["max_tokens"] <= 64
    # 探测不要求结构化输出，因此不带 json 相关参数。
    assert "response_format" not in sent
    assert "temperature" not in sent


@respx.mock
def test_probe_accepts_plain_text_reply_over_chat_completions(client: TestClient) -> None:
    """OpenAI 兼容协议同样只校验 envelope：探测不再依赖模型返回 JSON。"""

    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "The connection works."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
            },
        )
    )
    provider_id = api_provider(client, name="chat", protocol="chat_completions")
    tested = client.post(f"/api/llm/providers/{provider_id}/test", json={})
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] is True
    assert body["reply"] == "The connection works."
    assert body["total_tokens"] == 13


@respx.mock
def test_probe_still_rejects_broken_envelope(client: TestClient) -> None:
    """放开的只是业务结构：上游回 200 但不是协议形状时，探测仍然判失败。"""

    respx.post("https://llm.test/v1/messages").mock(
        return_value=httpx.Response(200, json={"reply": "not an anthropic envelope"})
    )
    provider_id = api_provider(client)
    body = client.post(f"/api/llm/providers/{provider_id}/test", json={}).json()
    assert body["ok"] is False
    assert body["error_code"] == "llm_invalid_output"
    assert body["reply"] == ""
    assert "secret-main" not in json.dumps(body, ensure_ascii=False)
