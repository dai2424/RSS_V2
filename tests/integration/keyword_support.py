"""关键词测试的共用夹具：造 RSS、配模型、跑 worker、假 provider。

检索、合并与统计三个测试文件都要造同样的数据；这是第三份同构实现出现之前抽出的公共层。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from rss_v2.bootstrap import Container
from rss_v2.domain import (
    EnrichmentResult,
    Keyword,
    KeywordKind,
    Provider,
    TranslationResult,
)
from rss_v2.llm import PromptPayload
from rss_v2.tasks.worker import Worker

CATEGORY_ID = "8cabd1f6-c0c3-4b32-863d-3836cf5a8171"

#: 足够长的描述：采集后会自动入队内容加工，测试不必逐个手动触发。
LONG_TEXT = "这段描述足够长，用来触发内容加工任务。" * 20


def feed(items: list[tuple[str, str, str]]) -> bytes:
    """按 (guid, 标题, 发布时间) 生成最小 RSS。"""

    entries = "".join(
        f"""<item>
  <guid>{guid}</guid>
  <title>{title}</title>
  <link>https://example.test/{guid}</link>
  <description><![CDATA[{LONG_TEXT}]]></description>
  <pubDate>{published}</pubDate>
</item>
"""
        for guid, title, published in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example Tech</title>
<link>https://example.test/</link>
<description>Example feed</description>
{entries}</channel></rss>"""


def create_source(client: TestClient, url: str, name: str = "Example Tech") -> str:
    response = client.post(
        "/api/sources",
        json={
            "name": name,
            "url": url,
            "platform": "Example",
            "language": "auto",
            "category_id": CATEGORY_ID,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def configure_model(client: TestClient) -> None:
    """配置一个可用 Provider×模型×Key，使加工任务能够入队。"""

    provider = client.post(
        "/api/llm/providers", json={"name": "primary", "base_url": "https://llm.test/v1"}
    )
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()["id"]
    assert (
        client.post(f"/api/llm/providers/{provider_id}/models", json={"model": "test-model"})
    ).status_code == 201
    assert (
        client.post(f"/api/llm/providers/{provider_id}/keys", json={"secret": "secret-main"})
    ).status_code == 201


def disable_enrich(client: TestClient, source_id: str) -> None:
    """只留手动入队，避免自动任务干扰候选统计。"""

    response = client.put(
        f"/api/task-settings/source/{source_id}",
        json={"settings": [{"task_kind": "enrich_message", "enabled": False}]},
    )
    assert response.status_code == 200, response.text


def collect(client: TestClient, source_id: str) -> Worker:
    """触发采集并执行采集任务，返回可继续执行后续任务的 worker。"""

    run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert run.status_code == 202, run.text
    worker = Worker(client.app.state.container)
    assert worker.run_once() is True
    return worker


class KeywordProvider:
    """按提示词里出现的关键字返回不同关键词的假模型。

    mapping 的键是标题里的特征串（如 "尊界V800"），值是该消息要产出的关键词元组；
    没有命中的消息用 default。
    """

    def __init__(
        self,
        mapping: dict[str, tuple[Keyword, ...]] | None = None,
        default: tuple[Keyword, ...] | None = None,
    ) -> None:
        self.mapping = mapping or {}
        self.default = default or (Keyword("OpenAI", KeywordKind.ENTITY),)
        self.calls = 0

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        return TranslationResult("中文标题", "中文摘要", "中文正文"), {}, 1

    def probe(
        self, provider: Provider, model: str, secret: str, prompt: PromptPayload
    ) -> tuple[str, dict[str, int], int]:
        return "ok", {}, 1

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]:
        self.calls += 1
        keywords = self.default
        for marker, matched in self.mapping.items():
            if marker in prompt.user:
                keywords = matched
                break
        return EnrichmentResult("精简标题", "中文摘要。", keywords), {"total_tokens": 5}, 1


def enrich_all(client: TestClient, fake: KeywordProvider) -> None:
    """逐条入队并执行，直到没有排队的模型任务。"""

    container: Container = client.app.state.container
    container.enrichment_service.provider = fake
    worker = Worker(container)
    for message in client.get("/api/messages").json():
        created = client.post(f"/api/messages/{message['id']}/enrich")
        assert created.status_code == 202, created.text
    while worker.run_once():
        pass


def search(client: TestClient, **params: str) -> list[dict[str, Any]]:
    """按查询参数取消息列表，顺带断言请求成功。"""

    response = client.get("/api/messages", params=params)
    assert response.status_code == 200, response.text
    return list(response.json())
