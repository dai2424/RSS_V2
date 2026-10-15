"""资料准备集成测试：检索式 → 读原文 → 联网补充 → 去重 → 预算与缺口 → 重跑恢复。

用固定的假检索、假读取与假模型验证服务端的确定性规则：命中只是候选、读取成功才算资料、
失败与未读取都要留痕、重跑不重复产生资料也不重复付费。
"""

from __future__ import annotations

from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import collect, configure_model, create_source, disable_enrich

from rss_v2.domain import (
    BUDGET_EXHAUSTED,
    ArticleRead,
    DomainError,
    ExternalServiceError,
    MaterialLimits,
    MaterialQueries,
    MaterialStatus,
    ProjectStatus,
    Provider,
    SearchHit,
    SearchOutcome,
    WritingProject,
)
from rss_v2.domain.values import new_id, now

QUERY = "尊界 V800 制动 争议"


def _two_item_feed() -> bytes:
    """两条消息、正文各不相同的 RSS。

    正文不同才能验证"不同资料不会被内容指纹去重"；`keyword_support.feed` 给所有条目
    同一段描述，会把两条成员折成一条。
    """

    bodies = [
        (
            "m1",
            "尊界 V800 制动测试引争议",
            "Wed, 07 Oct 2026 02:41:00 GMT",
            "懂车帝实测中三台车支架断裂。" * 8,
        ),
        (
            "m2",
            "尊界回应制动踏板问题",
            "Wed, 07 Oct 2026 03:02:00 GMT",
            "厂商称按高于国标要求验证，将免费升级。" * 8,
        ),
    ]
    entries = "".join(
        f"""<item>
  <guid>{guid}</guid>
  <title>{title}</title>
  <link>https://example.test/{guid}</link>
  <description><![CDATA[{text}]]></description>
  <pubDate>{published}</pubDate>
</item>
"""
        for guid, title, published, text in bodies
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Materials</title>
<link>https://example.test/</link>
<description>Materials fixture</description>
{entries}</channel></rss>""".encode()


class QueryProvider:
    """假模型：返回预设检索式；可模拟上游失败。"""

    def __init__(
        self, queries: list[tuple[str, str]] | None = None, error: Exception | None = None
    ):
        self.queries = queries or [(QUERY, "中文检索"), ("Zunjie V800 brake", "英文检索")]
        self.error = error
        self.calls = 0

    def structured(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: Any,
        prompt_version: str,
        task_kind: str,
        schema: type[Any],
        max_output_tokens: int | None = None,
    ) -> tuple[Any, dict[str, int], int]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        payload = MaterialQueries.model_validate(
            {"queries": [{"text": text, "purpose": purpose} for text, purpose in self.queries]}
        )
        return payload, {"total_tokens": 5}, 3


class FakeSearch:
    """假检索：按检索式返回预设命中；可模拟未响应引擎与失败。"""

    def __init__(self, hits: dict[str, list[SearchHit]], unresponsive: tuple[str, ...] = ()):
        self.hits = hits
        self.unresponsive = unresponsive
        self.calls: list[str] = []

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> SearchOutcome:
        self.calls.append(query)
        return SearchOutcome(
            hits=tuple(self.hits.get(query, [])[:limit]), unresponsive_engines=self.unresponsive
        )


class FakeReader:
    """假读取：按 URL 返回预设结果；未登记的地址按失败处理。"""

    def __init__(self, pages: dict[str, ArticleRead]):
        self.pages = pages
        self.calls: list[str] = []

    def read(self, url: str) -> ArticleRead:
        self.calls.append(url)
        page = self.pages.get(url)
        if page is not None:
            return page
        return ArticleRead(
            status=MaterialStatus.FAILED.value,
            final_url="",
            title="",
            publisher="",
            published_at=None,
            text="",
            content_hash="",
            byte_size=0,
            error_code="article_http_error",
            error_message="读取返回 HTTP 503",
        )


def _page(
    url: str, text: str, title: str = "外部报道", published: int | None = 1791420000
) -> ArticleRead:
    import hashlib

    return ArticleRead(
        status=MaterialStatus.READ.value,
        final_url=url,
        title=title,
        publisher="example.test",
        published_at=published,
        text=text,
        content_hash=hashlib.sha256(" ".join(text.split()).encode()).hexdigest(),
        byte_size=len(text.encode()),
    )


def _seed_project(client: TestClient, title: str = "尊界 V800 制动争议") -> str:
    """建两条消息 → 人工线索 → 写作项目；返回 project_id。"""

    marker = f"sources-{title[:6]}"
    url = f"https://materials.test/{marker}.xml"
    respx.get(url).mock(return_value=Response(200, content=_two_item_feed()))
    source_id = create_source(client, url, name=f"资料验收来源{title[:4]}")
    disable_enrich(client, source_id)
    collect(client, source_id)
    messages = client.get("/api/messages", params={"limit": "10", "source_id": source_id}).json()[
        "items"
    ]
    message_ids = [str(item["id"]) for item in messages]
    lead = client.post(
        "/api/leads",
        json={"title": title, "message_ids": message_ids, "note": "同一事件的两篇报道"},
    ).json()
    container = client.app.state.container
    timestamp = now()
    project = container.writing_projects.create(
        WritingProject(
            id=new_id(),
            lead_id=str(lead["id"]),
            candidate_id=None,
            title=title,
            status=ProjectStatus.MATERIALS_PENDING.value,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    return project.id


def _stub(client: TestClient, *, search: FakeSearch, reader: FakeReader, provider: QueryProvider):
    service = client.app.state.container.material_service
    service.search = search
    service.reader = reader
    service.provider = provider
    return service


def _prepare(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.post(f"/api/projects/{project_id}/materials")
    assert response.status_code == 202, response.text
    worker = __import__("rss_v2.tasks.worker", fromlist=["Worker"]).Worker(
        client.app.state.container
    )
    assert worker.run_once() is True
    return client.get(f"/api/projects/{project_id}/materials").json()


@respx.mock
def test_materials_snapshot_and_summary(client: TestClient) -> None:
    """RSS 原文与联网补充都落成资料快照；摘要给出检索式与各状态计数。"""

    configure_model(client)
    project_id = _seed_project(client)
    search = FakeSearch(
        {
            QUERY: [
                SearchHit(
                    title="懂车帝实测报道",
                    url="https://news.test/zunjie?utm_source=x",
                    snippet="片段",
                    engine="bing",
                )
            ],
            "Zunjie V800 brake": [
                SearchHit(
                    title="官方说明",
                    url="https://maker.test/statement",
                    snippet="片段",
                    engine="yandex",
                ),
                SearchHit(
                    title="打不开的页面",
                    url="https://broken.test/404",
                    snippet="片段",
                    engine="bing",
                ),
            ],
        }
    )
    reader = FakeReader(
        {
            # 夹具消息只有摘要（正文为空），服务会回读它们的链接补齐正文。
            "https://example.test/m1": _page("https://example.test/m1", "消息一原文" * 100),
            "https://example.test/m2": _page("https://example.test/m2", "消息二原文" * 100),
            "https://news.test/zunjie": _page("https://news.test/zunjie", "报道正文" * 200),
            "https://maker.test/statement": _page("https://maker.test/statement", "官方说明" * 200),
        }
    )
    provider = QueryProvider()
    _stub(client, search=search, reader=reader, provider=provider)

    body = _prepare(client, project_id)
    counts = body["counts"]
    # 4 条读到正文（两条成员链接回读 + 两篇外部网页）、2 条只有摘要、1 条打不开。
    assert (counts["read"], counts["summary_only"], counts["failed"]) == (4, 2, 1)
    summary = body["summary"]
    assert [item["query"] for item in summary["queries"]] == [QUERY, "Zunjie V800 brake"]
    assert summary["queries"][0]["hits"] == 1
    assert (summary["read"], summary["summary_only"], summary["failed"]) == (4, 2, 1)
    assert provider.calls == 1  # 检索式只生成一次

    items = body["items"]
    web = [item for item in items if item["kind"] == "web"]
    # 成员链接的回读也记在 web 资料里，并保留消息版本来源，便于回到订阅原文核对。
    rereads = [item for item in web if item["message_version_id"]]
    assert [item["url"] for item in rereads] == [
        "https://example.test/m1",
        "https://example.test/m2",
    ]  # 成员按发布时间从早到晚，回读顺序一致
    external = [item for item in web if not item["message_version_id"]]
    assert {item["url"] for item in external} == {
        "https://news.test/zunjie",
        "https://maker.test/statement",
        "https://broken.test/404",
    }
    assert all(item["fetched_at"] for item in web)
    readable = next(item for item in external if item["url"] == "https://news.test/zunjie")
    assert readable["content_hash"] and readable["published_at"] == 1791420000
    broken = next(item for item in external if item["url"] == "https://broken.test/404")
    assert broken["error_code"] == "article_http_error"

    detail = client.get(f"/api/projects/{project_id}/materials/{readable['id']}").json()
    assert "报道正文" in detail["content_text"]

    # 项目状态与摘要：资料就绪，列表里能看到在途任务状态（此处已完成）。
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["project"]["status"] == ProjectStatus.MATERIALS_READY.value
    assert body["task_status"] == "succeeded"
    assert project["counts"]["materials"] == 7  # 2 条 RSS 摘要 + 2 条回读 + 3 条检索命中


@respx.mock
def test_dedupe_and_rerun_skip_read_urls(client: TestClient) -> None:
    """同一地址（含跟踪参数）与同一内容只保留一条；重跑跳过已读地址，不重复付费。"""

    configure_model(client)
    project_id = _seed_project(client, title="去重验收选题")
    page = _page("https://news.test/a", "同一篇正文" * 100)
    search = FakeSearch(
        {
            QUERY: [
                SearchHit(
                    title="带参数",
                    url="https://news.test/a?utm_source=x",
                    snippet="",
                    engine="bing",
                ),
                SearchHit(title="无参数", url="https://news.test/a", snippet="", engine="yandex"),
            ]
        }
    )
    reader = FakeReader(
        {
            "https://example.test/m1": _page("https://example.test/m1", "消息一原文" * 100),
            "https://example.test/m2": _page("https://example.test/m2", "消息二原文" * 100),
            "https://news.test/a": page,
        }
    )
    _stub(client, search=search, reader=reader, provider=QueryProvider())

    first = _prepare(client, project_id)
    # 第二个命中被规范化去重（带 utm 参数视为同一地址），没有再次读取。
    assert reader.calls.count("https://news.test/a") == 1
    assert first["summary"]["duplicates"] == 1
    web = [item for item in first["items"] if item["kind"] == "web"]
    assert [item["url"] for item in web].count("https://news.test/a") == 1

    # 重新发起（同一批消息）：任务复用已完成的幂等键，不重复读取。
    before = list(reader.calls)
    again = client.post(f"/api/projects/{project_id}/materials")
    assert again.status_code == 202
    assert again.json()["status"] == "succeeded"
    assert reader.calls == before


@respx.mock
def test_budget_exhausted_keeps_gap_visible(client: TestClient) -> None:
    """读取预算用完时留下"未读取"占位行与缺口说明，不静默丢候选。"""

    configure_model(client)
    project_id = _seed_project(client, title="预算验收选题")
    hits = [
        SearchHit(
            title=f"候选 {index}", url=f"https://news.test/{index}", snippet="", engine="bing"
        )
        for index in range(4)
    ]
    search = FakeSearch({QUERY: hits})
    pages = {
        f"https://news.test/{index}": _page(f"https://news.test/{index}", f"第 {index} 篇正文" * 50)
        for index in range(4)
    }
    pages["https://example.test/m1"] = _page("https://example.test/m1", "消息一原文" * 100)
    pages["https://example.test/m2"] = _page("https://example.test/m2", "消息二原文" * 100)
    reader = FakeReader(pages)
    service = _stub(client, search=search, reader=reader, provider=QueryProvider())
    # 预算 4 = 两条成员链接回读 + 两条外部资料；其余候选落成"未读取"。
    service.limits = MaterialLimits(max_reads=4, search_result_limit=4)

    body = _prepare(client, project_id)
    # 预算内读了 4 条（两条成员链接 + 两条外部资料），其余候选留成"未读取"。
    assert body["summary"]["read"] == 4
    assert body["summary"]["skipped_by_budget"] == 2
    assert body["summary"]["notes"] and "读取上限" in body["summary"]["notes"][0]
    skipped = [item for item in body["items"] if item["error_code"] == BUDGET_EXHAUSTED]
    assert len(skipped) == 2
    assert all(item["status"] == MaterialStatus.FAILED.value for item in skipped)


@respx.mock
def test_single_material_failure_does_not_fail_task(client: TestClient) -> None:
    """单份资料读取失败不失败整个任务，其余资料照常保留。"""

    configure_model(client)
    project_id = _seed_project(client, title="部分失败选题")
    search = FakeSearch(
        {
            QUERY: [
                SearchHit(title="好页面", url="https://news.test/ok", snippet="", engine="bing"),
                SearchHit(title="坏页面", url="https://news.test/bad", snippet="", engine="bing"),
            ]
        }
    )
    reader = FakeReader(
        {
            "https://example.test/m1": _page("https://example.test/m1", "消息一原文" * 100),
            "https://example.test/m2": _page("https://example.test/m2", "消息二原文" * 100),
            "https://news.test/ok": _page("https://news.test/ok", "可用正文" * 100),
        }
    )
    _stub(client, search=search, reader=reader, provider=QueryProvider())

    body = _prepare(client, project_id)
    statuses = {item["url"]: item["status"] for item in body["items"] if item["kind"] == "web"}
    assert statuses["https://news.test/ok"] == MaterialStatus.READ.value
    assert statuses["https://news.test/bad"] == MaterialStatus.FAILED.value
    assert body["task_status"] == "succeeded"


@respx.mock
def test_search_failure_retries_task_with_reason(client: TestClient) -> None:
    """检索失败（未配置服务地址等）按现有阶梯重试并保留分类，不伪装成"没有资料"。"""

    configure_model(client)
    project_id = _seed_project(client, title="检索失败选题")

    class BrokenSearch:
        def search(self, query: str, *, limit: int, time_range: str | None = None):
            raise ExternalServiceError("search_unconfigured", "没有配置检索服务地址")

    service = client.app.state.container.material_service
    service.search = BrokenSearch()
    service.reader = FakeReader({})
    service.provider = QueryProvider()

    response = client.post(f"/api/projects/{project_id}/materials")
    assert response.status_code == 202
    task_id = response.json()["id"]
    worker = __import__("rss_v2.tasks.worker", fromlist=["Worker"]).Worker(
        client.app.state.container
    )
    assert worker.run_once() is True
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "queued"  # 可重试：等待冷却
    assert task["attempts"] == 1
    assert task["error_code"] == "search_unconfigured"
    body = client.get(f"/api/projects/{project_id}/materials").json()
    # 检索失败不影响已经写入的 RSS 资料（重试时按地址跳过，不会重复读）。
    rss = [item for item in body["items"] if item["kind"] == "rss"]
    assert len(rss) == 2
    assert all(item["status"] == MaterialStatus.SUMMARY_ONLY.value for item in rss)


@respx.mock
def test_expired_lease_blocks_late_material_write(client: TestClient) -> None:
    """租约失效后继续执行会报 task_lease_lost，晚到的资料不能写进项目。"""

    configure_model(client)
    project_id = _seed_project(client, title="租约验收选题")
    container = client.app.state.container
    _stub(
        client,
        search=FakeSearch(
            {QUERY: [SearchHit(title="x", url="https://news.test/x", snippet="", engine="bing")]}
        ),
        reader=FakeReader({"https://news.test/x": _page("https://news.test/x", "正文" * 100)}),
        provider=QueryProvider(),
    )
    response = client.post(f"/api/projects/{project_id}/materials")
    task = container.tasks.get(str(response.json()["id"]))
    claimed = container.tasks.claim_next(["prepare_materials"], int(task.created_at), 300)
    assert claimed is not None
    container.tasks.reclaim_expired(int(claimed.lease_until or 0) + 1)

    import pytest

    with pytest.raises(DomainError) as raised:
        container.material_service.run(claimed)
    assert raised.value.code == "task_lease_lost"
    body = client.get(f"/api/projects/{project_id}/materials").json()
    assert body["counts"]["total"] == 0  # 一条资料都没写进去


@respx.mock
def test_missing_search_config_fails_clearly(client: TestClient) -> None:
    """未配置检索服务时，任务失败信息必须指出缺什么，而不是"没有资料"。"""

    configure_model(client)
    project_id = _seed_project(client, title="未配置检索选题")
    container = client.app.state.container
    service = container.material_service
    service.provider = QueryProvider()

    # 用真实适配器（地址为空）验证失败文案。
    from rss_v2.adapters.web.searxng import HTTPXSearchClient

    service.search = HTTPXSearchClient("", 5.0)
    response = client.post(f"/api/projects/{project_id}/materials")
    assert response.status_code == 202
    worker = __import__("rss_v2.tasks.worker", fromlist=["Worker"]).Worker(container)
    assert worker.run_once() is True
    task = client.get(f"/api/tasks/{response.json()['id']}").json()
    assert task["error_code"] == "search_unconfigured"
    assert "search_base_url" in task["error_message"]
