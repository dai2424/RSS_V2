"""关键词检索、相关消息、索引重建与存量回填的集成测试。"""

from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import (
    KeywordProvider,
    collect,
    configure_model,
    create_source,
    disable_enrich,
    enrich_all,
    feed,
    search,
)

from rss_v2.bootstrap import Container
from rss_v2.domain import Keyword, KeywordKind


def provider() -> KeywordProvider:
    """按标题返回不同关键词：两条共享实体"尊界"，一条只谈 OpenAI。"""

    return KeywordProvider(
        mapping={
            "尊界V800": (
                Keyword("尊界", KeywordKind.ENTITY),
                Keyword("尊界V800", KeywordKind.ENTITY),
                Keyword("刹车测试", KeywordKind.EVENT),
            ),
            "尊界S800": (
                Keyword("尊界", KeywordKind.ENTITY),
                Keyword("尊界S800", KeywordKind.ENTITY),
                Keyword("交付", KeywordKind.TOPIC),
            ),
        },
        default=(Keyword("OpenAI", KeywordKind.ENTITY), Keyword("定价", KeywordKind.TOPIC)),
    )


@respx.mock
def test_list_orders_by_published_time_not_collection(client: TestClient) -> None:
    """列表按发布时间排序：稍后被重新采集的旧闻不应该顶到最前面。"""

    url = "https://order.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("old", "2020 年的旧闻", "Wed, 01 Jan 2020 00:00:00 GMT"),
                    ("new", "今天的消息", "Thu, 08 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    source_id = create_source(client, url)
    collect(client, source_id)
    assert [item["latest_version"]["title"] for item in search(client)] == [
        "今天的消息",
        "2020 年的旧闻",
    ]

    # 旧闻出现新版本：入库时间变成最新，但发布时间没变，仍应排在后面。
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("old", "2020 年的旧闻（更新）", "Wed, 01 Jan 2020 00:00:00 GMT"),
                    ("new", "今天的消息", "Thu, 08 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    collect(client, source_id)
    assert [item["latest_version"]["title"] for item in search(client)] == [
        "今天的消息",
        "2020 年的旧闻（更新）",
    ]


@respx.mock
def test_since_and_until_filter_by_published_time(client: TestClient) -> None:
    """时间范围筛选按发布时间生效，边界包含端点。"""

    url = "https://window.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("old", "2020 年的旧闻", "Wed, 01 Jan 2020 00:00:00 GMT"),
                    ("new", "今天的消息", "Thu, 08 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    source_id = create_source(client, url)
    collect(client, source_id)

    def titles(rows: list[dict[str, Any]]) -> list[str]:
        return [str(item["latest_version"]["title"]) for item in rows]

    assert titles(search(client, since="1600000000")) == ["今天的消息"]
    assert titles(search(client, until="1600000000")) == ["2020 年的旧闻"]
    # 2025 年的时间窗：两条消息都不在范围内。
    assert search(client, since="1750000000", until="1758000000") == []


@respx.mock
def test_keyword_search_is_normalized_and_kind_aware(client: TestClient) -> None:
    """关键词检索按归一化匹配键：大小写与全角差异命中同一个词，类型可收窄。"""

    url = "https://keyword.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("zunjie", "尊界V800 刹车测试", "Thu, 08 Oct 2026 00:00:00 GMT"),
                    ("zunjie2", "尊界S800 交付", "Wed, 07 Oct 2026 00:00:00 GMT"),
                    ("openai", "OpenAI 定价", "Tue, 06 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    configure_model(client)
    source_id = create_source(client, url)
    collect(client, source_id)
    enrich_all(client, provider())

    # 前缀命中同一主体的不同粒度。
    assert len(search(client, keyword="尊界")) == 2
    assert len(search(client, keyword="尊界V800")) == 1
    # 大小写与全角差异落在一个匹配键上。
    assert len(search(client, keyword="openai")) == 1
    assert len(search(client, keyword="ＯｐｅｎＡＩ")) == 1
    # 旧实现把 LIKE 打在该词的 JSON 文本上，"AI" 会命中 "OpenAI"；现在不会。
    assert search(client, keyword="AI") == []
    # 类型收窄：定价是主题词，不是实体。
    assert len(search(client, keyword="定价", kind="topic")) == 1
    assert search(client, keyword="定价", kind="entity") == []
    # 纯标点没有可匹配的字面内容，明确返回空集而不是忽略条件。
    assert search(client, keyword="···") == []


@respx.mock
def test_related_messages_rank_shared_entities(client: TestClient) -> None:
    """相关消息按共享关键词聚合：共享实体排在前面，没有共享词的不出现。"""

    url = "https://related.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("zunjie", "尊界V800 刹车测试", "Thu, 08 Oct 2026 00:00:00 GMT"),
                    ("zunjie2", "尊界S800 交付", "Wed, 07 Oct 2026 00:00:00 GMT"),
                    ("openai", "OpenAI 定价", "Tue, 06 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    configure_model(client)
    source_id = create_source(client, url)
    collect(client, source_id)
    enrich_all(client, provider())

    first = search(client)[0]
    related = client.get(f"/api/messages/{first['id']}/related")
    assert related.status_code == 200, related.text
    rows = related.json()
    assert [item["title"] for item in rows] == ["尊界S800 交付"]
    assert [item["text"] for item in rows[0]["shared"]] == ["尊界"]

    # 没有关键词的消息没有相关消息，而不是报错。
    alone = search(client, keyword="OpenAI")[0]
    assert client.get(f"/api/messages/{alone['id']}/related").json() == []


@respx.mock
def test_rebuild_indexes_legacy_keyword_snapshots(client: TestClient) -> None:
    """结构化改造之前的结果只有 JSON：重建索引后同样可以检索。"""

    url = "https://legacy.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200, content=feed([("legacy", "旧条目", "Thu, 08 Oct 2026 00:00:00 GMT")])
        )
    )
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    collect(client, source_id)
    container: Container = client.app.state.container
    message = search(client)[0]
    version_id = message["latest_version"]["id"]
    # 老行的关键词是字符串数组，没有 kind；直接写库模拟升级前的存量结果。
    with container.database.transaction() as connection:
        connection.execute(
            "INSERT INTO message_enrichments(id,message_version_id,status,title,summary,"
            "keywords_json,model,prompt_version,created_at,updated_at)"
            " VALUES('legacy-enrich',?,'succeeded','旧标题','旧摘要',"
            "'[\"OpenAI\",\"openai\",\"发布公告\"]','legacy-model','enrich-v1',1,1)",
            (version_id,),
        )
    assert search(client, keyword="OpenAI") == []

    rebuilt = client.post("/api/keywords/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    # 三个关键词里 "OpenAI" 与 "openai" 归一化后是同一个词，只写一行。
    assert rebuilt.json()["indexed"] == 2
    assert len(search(client, keyword="openai")) == 1
    assert len(search(client, keyword="发布公告")) == 1


@respx.mock
def test_backfill_previews_then_enqueues_idempotently(client: TestClient) -> None:
    """回填先预览再入队；重复执行复用同一批任务，不重复调用模型。"""

    url = "https://backfill.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("a", "第一条", "Thu, 08 Oct 2026 00:00:00 GMT"),
                    ("b", "第二条", "Wed, 07 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    configure_model(client)
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    collect(client, source_id)
    assert client.get("/api/tasks", params={"status": "queued"}).json()["items"] == []

    preview = client.post("/api/keywords/backfill", json={"dry_run": True})
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"candidates": 2, "enqueued": 0, "skipped": 0, "reasons": []}

    executed = client.post("/api/keywords/backfill", json={})
    assert executed.json()["enqueued"] == 2
    queued = client.get("/api/tasks", params={"status": "queued"}).json()["items"]
    assert len(queued) == 2

    again = client.post("/api/keywords/backfill", json={})
    assert again.json()["enqueued"] == 2
    assert len(client.get("/api/tasks", params={"status": "queued"}).json()["items"]) == 2


@respx.mock
def test_backfill_reports_reason_when_model_is_missing(client: TestClient) -> None:
    """没有可用模型配置时逐条跳过并给出原因，不抛 500。"""

    url = "https://nokey.test/feed"
    respx.get(url).mock(
        return_value=Response(200, content=feed([("a", "第一条", "Thu, 08 Oct 2026 00:00:00 GMT")]))
    )
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    collect(client, source_id)

    response = client.post("/api/keywords/backfill", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidates"] == 1 and body["enqueued"] == 0 and body["skipped"] == 1
    assert body["reasons"] == ["没有启用且具有可用 Key 的模型"]
