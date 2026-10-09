"""词表、概览与分布的集成测试：数字必须与造出来的数据一一对上。"""

from datetime import UTC, datetime, timedelta

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import (
    KeywordProvider,
    collect,
    configure_model,
    create_source,
    disable_enrich,
    drain_worker,
    feed,
    stub_providers,
)

from rss_v2.domain import Keyword, KeywordKind


def rfc2822(days_ago: int) -> str:
    """相对当前时间的 RFC 2822 日期；趋势按真实日历天分桶，测试不能依赖固定日期。"""

    moment = datetime.now(tz=UTC) - timedelta(days=days_ago)
    return moment.strftime("%a, %d %b %Y %H:%M:%S GMT")


def provider() -> KeywordProvider:
    return KeywordProvider(
        mapping={
            "AWS 与云服务": (
                Keyword("AWS", KeywordKind.ENTITY),
                Keyword("云服务", KeywordKind.TOPIC),
            ),
            "AWS 交付": (
                Keyword("AWS", KeywordKind.ENTITY),
                Keyword("交付", KeywordKind.TOPIC),
            ),
            "谷歌云 扩容": (
                Keyword("谷歌云", KeywordKind.ENTITY),
                Keyword("云服务", KeywordKind.TOPIC),
            ),
        },
        default=(Keyword("独家词", KeywordKind.ENTITY),),
    )


def seed_two_sources(client: TestClient) -> tuple[str, str]:
    """两个来源四条消息：AWS 出现两次、云服务两次、谷歌云一次、独家词一次。"""

    first_url = "https://alpha.test/feed"
    second_url = "https://beta.test/feed"
    respx.get(first_url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("a1", "AWS 与云服务", rfc2822(0)),
                    ("a2", "AWS 交付", rfc2822(0)),
                ]
            ),
        )
    )
    respx.get(second_url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("b1", "谷歌云 扩容", rfc2822(1)),
                    ("b2", "独家消息", rfc2822(1)),
                ]
            ),
        )
    )
    configure_model(client)
    stub_providers(client, provider())
    alpha = create_source(client, first_url, "Alpha")
    beta = create_source(client, second_url, "Beta")
    for source_id in (alpha, beta):
        run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
        assert run.status_code == 202, run.text
    # 两个来源的采集与加工任务一起排空：内容够长时会自动入队加工。
    drain_worker(client)
    return alpha, beta


def overview(client: TestClient) -> dict[str, object]:
    response = client.get("/api/keywords/overview")
    assert response.status_code == 200, response.text
    return response.json()


def vocabulary(client: TestClient, **params: str) -> dict[str, object]:
    response = client.get("/api/keywords", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def terms(page: dict[str, object]) -> list[str]:
    return [str(item["text"]) for item in page["items"]]  # type: ignore[union-attr]


@respx.mock
def test_overview_and_vocabulary_match_seeded_data(client: TestClient) -> None:
    """概览的规模、长尾、类型构成与来源分布和造出来的数据一致。"""

    seed_two_sources(client)
    data = overview(client)
    assert (data["messages"], data["enriched"], data["pending"]) == (4, 4, 0)
    assert (data["terms"], data["mentions"], data["singletons"]) == (5, 7, 3)
    assert data["average_per_message"] == 1.75
    assert (data["aliases"], data["merges"]) == (0, 0)
    assert data["tasks_succeeded"] == 4 and data["tasks_queued"] == 0

    # 长尾：只出现一次的三个（交付、谷歌云、独家词），出现两次的两个（AWS、云服务）。
    assert [(item["label"], item["terms"]) for item in data["long_tail"]] == [
        ("只出现一次", 3),
        ("出现 2 次", 2),
        ("出现 3 次", 0),
        ("出现 4-5 次", 0),
        ("出现 6 次以上", 0),
    ]
    kinds = {item["kind"]: (item["terms"], item["mentions"]) for item in data["kinds"]}
    assert kinds == {"entity": (3, 4), "topic": (2, 3)}

    # 来源分布按词位数排序：Alpha 四条词位、Beta 三条。
    sources = [(item["source_name"], item["mentions"], item["terms"]) for item in data["sources"]]
    assert sources == [("Alpha", 4, 3), ("Beta", 3, 3)]
    assert [item["text"] for item in data["sources"][0]["top"]] == ["AWS", "云服务", "交付"]

    # 趋势：近三天有数据，今天的词位数包含两条消息的关键词。
    trend = {item["day"]: item for item in data["trend"]}
    today = datetime.now(tz=UTC).date().isoformat()
    yesterday = (datetime.now(tz=UTC).date() - timedelta(days=1)).isoformat()
    assert trend[today]["mentions"] == 4
    assert trend[yesterday]["mentions"] == 3
    assert len(data["trend"]) == 14

    # 词表默认折叠只出现一次的词；总数与筛选条件同一口径。
    page = vocabulary(client)
    assert (page["total"], terms(page)) == (2, ["AWS", "云服务"])
    assert [item["mentions"] for item in page["items"]] == [2, 2]

    everything = vocabulary(client, min_count="1")
    assert everything["total"] == 5
    # 同分按最近出现时间排序，再按词键的字节序：今天出现的交付在前。
    assert terms(everything) == ["AWS", "云服务", "交付", "独家词", "谷歌云"]

    assert terms(vocabulary(client, min_count="1", kind="entity")) == [
        "AWS",
        "独家词",
        "谷歌云",
    ]
    assert terms(vocabulary(client, min_count="1", q="云")) == ["云服务", "谷歌云"]

    page_one = vocabulary(client, min_count="1", limit="2")
    assert page_one["total"] == 5 and len(page_one["items"]) == 2
    page_two = vocabulary(client, min_count="1", limit="2", offset="2")
    assert len(page_two["items"]) == 2
    assert set(terms(page_one)) & set(terms(page_two)) == set()


@respx.mock
def test_pending_messages_match_backfill_candidates(client: TestClient) -> None:
    """覆盖进度里的待加工条数与回填候选数必须相等：同一个口径。"""

    url = "https://gamma.test/feed"
    respx.get(url).mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("c1", "AWS 与云服务", rfc2822(0)),
                    ("c2", "AWS 交付", rfc2822(0)),
                ]
            ),
        )
    )
    configure_model(client)
    source_id = create_source(client, url, "Gamma")
    disable_enrich(client, source_id)
    collect(client, source_id)

    data = overview(client)
    assert (data["messages"], data["enriched"], data["pending"]) == (2, 0, 2)
    assert (data["terms"], data["mentions"]) == (0, 0)
    assert data["average_per_message"] == 0.0

    preview = client.post("/api/keywords/backfill", json={"dry_run": True})
    assert preview.json()["candidates"] == data["pending"]


@respx.mock
def test_merge_shrinks_vocabulary_and_keeps_mentions(client: TestClient) -> None:
    """合并后规范词减少、词位数不变，别名与合并计数随之出现；撤销后复原。"""

    seed_two_sources(client)
    record = client.post("/api/keywords/merge", json={"keys": ["谷歌云"], "target": "AWS"}).json()[
        "record"
    ]

    data = overview(client)
    assert (data["terms"], data["mentions"]) == (4, 7)
    assert (data["aliases"], data["merges"]) == (1, 1)
    merged = vocabulary(client)
    assert terms(merged) == ["AWS", "云服务"]
    assert merged["items"][0]["mentions"] == 3
    assert merged["items"][0]["aliases"] == ["谷歌云"]
    # 类型裁决：entity 优先，合并后 AWS 仍是实体。
    assert merged["items"][0]["kind"] == "entity"

    assert client.post(f"/api/keywords/merges/{record['id']}/undo").status_code == 200
    restored = overview(client)
    assert (restored["terms"], restored["mentions"]) == (5, 7)
    assert (restored["aliases"], restored["merges"]) == (0, 0)
    # 默认折叠孤词，所以撤销后谷歌云不再出现在默认视图里。
    assert terms(vocabulary(client)) == ["AWS", "云服务"]
