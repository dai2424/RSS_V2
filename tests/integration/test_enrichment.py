"""内容加工任务、自动入队与检索的集成测试。"""

import respx
from fastapi.testclient import TestClient
from httpx import Response

from rss_v2.bootstrap import Container
from rss_v2.domain import EnrichmentResult, ExternalServiceError, Provider, TranslationResult
from rss_v2.tasks.worker import Worker

CATEGORY_ID = "8cabd1f6-c0c3-4b32-863d-3836cf5a8171"

#: 标题 44 字、摘要远超阈值，采集后既需要翻译也需要内容加工。
LONG_ENGLISH_TITLE = "A very long English headline that should be shortened before it reaches rank"
LONG_ENGLISH_SUMMARY = "This description repeats what the headline says and keeps going " * 12
SHORT_CHINESE_TITLE = "短标题"
SHORT_CHINESE_SUMMARY = "一句话说清楚。"
#: 中文长描述：不需要翻译，但仍超过加工阈值。
LONG_CHINESE_SUMMARY = "这是一段很长的中文描述，用来验证内容加工任务。" * 20


def feed(title: str, summary: str) -> bytes:
    """构造只提供 description 的最小 RSS；用于控制标题与摘要长度。"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example Tech</title>
<link>https://example.test/</link>
<description>Example feed</description>
<item>
  <guid>entry-1</guid>
  <title>{title}</title>
  <link>https://example.test/entry-1</link>
  <description><![CDATA[{summary}]]></description>
  <pubDate>Tue, 07 Oct 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>"""


def create_source(client: TestClient, url: str) -> str:
    response = client.post(
        "/api/sources",
        json={
            "name": "Example Tech",
            "url": url,
            "platform": "Example",
            "language": "auto",
            "category_id": CATEGORY_ID,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def configure_model(client: TestClient) -> None:
    """配置一个可用 Provider×模型×Key，使自动任务能够入队。"""

    provider = client.post(
        "/api/llm/providers", json={"name": "primary", "base_url": "https://llm.test/v1"}
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    created = client.post(f"/api/llm/providers/{provider_id}/models", json={"model": "test-model"})
    assert created.status_code == 201, created.text
    key = client.post(f"/api/llm/providers/{provider_id}/keys", json={"secret": "secret-main"})
    assert key.status_code == 201, key.text


class FakeProvider:
    """记录调用类别与输入，可按键模拟失败；翻译与加工返回不同结构。"""

    def __init__(self, failures: dict[str, str] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[str, str, str]] = []  # (类别, 模型, 密钥)

    def _check(self, kind: str, model: str, secret: str) -> None:
        self.calls.append((kind, model, secret))
        if code := self.failures.get(secret):
            raise ExternalServiceError(code, "模拟失败")

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        self._check("translate", model, secret)
        return TranslationResult("中文标题", "中文摘要", "中文正文"), {"total_tokens": 3}, 1

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        title: str,
        summary: str,
        content: str,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]:
        self._check("enrich", model, secret)
        return (
            EnrichmentResult("精简标题", "中文摘要说明发生了什么。", ("关键词甲", "关键词乙")),
            {"total_tokens": 5},
            1,
        )


def collect(client: TestClient, source_id: str) -> Worker:
    """触发采集并执行采集任务，返回可继续执行后续任务的 worker。"""

    run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert run.status_code == 202, run.text
    worker = Worker(client.app.state.container)
    assert worker.run_once() is True
    return worker


def queued_tasks(client: TestClient) -> list[dict[str, object]]:
    """只取排队中的模型任务，忽略已完成的历史采集任务。"""

    response = client.get("/api/tasks", params={"status": "queued"})
    assert response.status_code == 200, response.text
    return list(response.json())


def stub_fake(client: TestClient, fake: FakeProvider) -> None:
    container: Container = client.app.state.container
    container.translation_service.provider = fake
    container.enrichment_service.provider = fake


@respx.mock
def test_collection_enqueues_translation_and_enrichment(client: TestClient) -> None:
    """英文长消息采集后自动入队，worker 依次完成翻译与内容加工。"""
    respx.get("https://auto.test/feed").mock(
        return_value=Response(200, content=feed(LONG_ENGLISH_TITLE, LONG_ENGLISH_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://auto.test/feed")
    worker = collect(client, source_id)

    queued = queued_tasks(client)
    assert sorted(str(task["task_type"]) for task in queued) == [
        "enrich_message",
        "translate_message",
    ]

    fake = FakeProvider()
    stub_fake(client, fake)
    assert worker.run_once() is True
    assert worker.run_once() is True
    assert queued_tasks(client) == []

    message = client.get("/api/messages").json()[0]
    version = message["latest_version"]
    assert version["translations"][0]["title"] == "中文标题"
    assert version["enrichments"][0]["title"] == "精简标题"
    assert version["enrichments"][0]["keywords"] == ["关键词甲", "关键词乙"]
    assert version["enrichments"][0]["status"] == "succeeded"
    # 任务完成后不再排队，列表按任务类型分别展示真实状态。
    assert version["translation_task"]["status"] == "succeeded"
    assert version["enrichment_task"]["status"] == "succeeded"
    assert sorted(call[0] for call in fake.calls) == ["enrich", "translate"]


@respx.mock
def test_short_chinese_message_skips_model_calls(client: TestClient) -> None:
    """中文短消息既不需要翻译也不需要加工，采集后不产生模型任务。"""
    respx.get("https://short.test/feed").mock(
        return_value=Response(200, content=feed(SHORT_CHINESE_TITLE, SHORT_CHINESE_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://short.test/feed")
    collect(client, source_id)

    assert queued_tasks(client) == []


@respx.mock
def test_collection_survives_missing_model_configuration(client: TestClient) -> None:
    """没有可用 Key 时自动处理只跳过任务，采集本身照常成功。"""
    respx.get("https://nokey.test/feed").mock(
        return_value=Response(200, content=feed(LONG_ENGLISH_TITLE, LONG_ENGLISH_SUMMARY))
    )
    source_id = create_source(client, "https://nokey.test/feed")
    collect(client, source_id)

    assert queued_tasks(client) == []
    messages = client.get("/api/messages").json()
    assert len(messages) == 1
    assert messages[0]["latest_version"]["title"] == LONG_ENGLISH_TITLE


@respx.mock
def test_enrichment_reuses_task_and_keeps_failure_reason(client: TestClient) -> None:
    """中文长消息只入队内容加工；同版本重复入队不重复调用，失败保留原因。"""
    respx.get("https://retry.test/feed").mock(
        return_value=Response(200, content=feed(SHORT_CHINESE_TITLE, LONG_CHINESE_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://retry.test/feed")
    worker = collect(client, source_id)
    message_id = client.get("/api/messages").json()[0]["id"]

    queued = queued_tasks(client)
    assert [str(task["task_type"]) for task in queued] == ["enrich_message"]
    created = client.post(f"/api/messages/{message_id}/enrich")
    assert created.status_code == 202, created.text
    assert client.post(f"/api/messages/{message_id}/enrich").json()["id"] == created.json()["id"]
    assert len(queued_tasks(client)) == 1

    fake = FakeProvider({"secret-main": "rate_limited"})
    stub_fake(client, fake)
    assert worker.run_once() is True
    # 限流属于临时错误：保留原因并进入自动重试冷却，而不是直接判定失败。
    assert fake.calls == [("enrich", "test-model", "secret-main")]
    retried = client.get(f"/api/tasks/{created.json()['id']}").json()
    assert retried["status"] == "queued"
    assert retried["error_code"] == "rate_limited"
    # 冷却未到期前不会立刻重跑，避免忙循环。
    assert worker.run_once() is False


@respx.mock
def test_search_matches_generated_content(client: TestClient) -> None:
    """中文检索命中机器生成内容，不只是原文。"""
    respx.get("https://search.test/feed").mock(
        return_value=Response(200, content=feed(LONG_ENGLISH_TITLE, LONG_ENGLISH_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://search.test/feed")
    worker = collect(client, source_id)
    stub_fake(client, FakeProvider())
    assert worker.run_once() is True
    assert worker.run_once() is True

    hit = client.get("/api/messages", params={"q": "关键词乙"}).json()
    assert len(hit) == 1
    assert hit[0]["latest_version"]["enrichments"][0]["summary"] == "中文摘要说明发生了什么。"
    translated = client.get("/api/messages", params={"q": "中文正文"}).json()
    assert len(translated) == 1
    assert client.get("/api/messages", params={"q": "不存在的词"}).json() == []
