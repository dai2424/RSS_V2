"""常驻词：必然注入（含超出条数上限）、只看常驻筛选、与合并/撤销的联动。"""

from pathlib import Path

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
    search,
    stub_providers,
)

from rss_v2.api.app import create_app
from rss_v2.domain import Keyword, KeywordKind
from rss_v2.settings import Settings

#: 按发布时间从新到旧；测试按"计算平台 → 存储芯片 → 存储设备"的顺序加工。
FEED = [
    ("platform", "计算平台发布", "Sat, 10 Oct 2026 00:00:00 GMT"),
    ("chip", "存储芯片涨价", "Fri, 09 Oct 2026 00:00:00 GMT"),
    ("storage", "存储设备降价", "Thu, 08 Oct 2026 00:00:00 GMT"),
]


def provider() -> KeywordProvider:
    return KeywordProvider(
        mapping={
            "计算平台": (
                Keyword("AWS", KeywordKind.ENTITY),
                Keyword("发布", KeywordKind.EVENT),
            ),
        },
        default=(Keyword("其它", KeywordKind.TOPIC),),
    )


def build_client(tmp_path: Path, vocabulary_limit: int) -> TestClient:
    """条数上限由配置决定，超出上限的常驻词是否出现只能在小上限下验证。

    近期窗口关掉：新词本来就有一条"近期新增"通道，会把对照组的词也带进来，
    这里只验证常驻这一条通道。
    """

    settings = Settings(
        runtime_dir=tmp_path / "runtime",
        database_path=tmp_path / "runtime" / "db.sqlite",
        migrations_dir=Path("migrations"),
        llm_vocabulary_limit=vocabulary_limit,
        llm_vocabulary_recent_days=0,
    )
    return TestClient(create_app(settings))


def pin(client: TestClient, key: str, pinned: bool) -> dict[str, object]:
    response = client.post("/api/keywords/pin", json={"key": key, "pinned": pinned})
    assert response.status_code == 200, response.text
    return dict(response.json())


def message_id(client: TestClient, title: str) -> str:
    for item in search(client, limit="50"):
        if item["latest_version"]["title"] == title:
            return str(item["id"])
    raise AssertionError(f"没有找到标题为 {title} 的消息")


def enrich_one(client: TestClient, message_id: str) -> None:
    created = client.post(f"/api/messages/{message_id}/enrich")
    assert created.status_code == 201 or created.status_code == 202, created.text
    drain_worker(client)


def seeded(client: TestClient, fake: KeywordProvider, url: str) -> None:
    respx.get(url).mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    stub_providers(client, fake)
    collect(client, source_id)


@respx.mock
def test_pinned_word_is_injected_even_beyond_the_limit(tmp_path: Path) -> None:
    """注入条数上限为 1 时，没被标记的词不进提示词，被标记的词必然出现。"""

    fake = provider()
    with build_client(tmp_path, vocabulary_limit=1) as client:
        seeded(client, fake, "https://pin.test/feed")
        enrich_one(client, message_id(client, "计算平台发布"))

        # 标记之前：上限为 1，只出现频次最高的写法，事件词不进提示词。
        enrich_one(client, message_id(client, "存储芯片涨价"))
        before = fake.prompts[-1].user
        assert "实体：AWS" in before
        assert "发布" not in before

        pinned = pin(client, "发布", True)
        assert pinned == {"key": "发布", "pinned": True}

        # 标记之后：常驻词即使超出上限也带上。
        enrich_one(client, message_id(client, "存储设备降价"))
        assert "事件：发布" in fake.prompts[-1].user


@respx.mock
def test_pin_is_listed_and_filterable(client: TestClient) -> None:
    """词表行带常驻标记，可按常驻筛选；取消标记后恢复。"""

    fake = provider()
    respx.get("https://pin-filter.test/feed").mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    source_id = create_source(client, "https://pin-filter.test/feed")
    disable_enrich(client, source_id)
    stub_providers(client, fake)
    collect(client, source_id)
    enrich_one(client, message_id(client, "计算平台发布"))

    all_terms = client.get("/api/keywords", params={"min_count": 1}).json()
    assert [item["pinned"] for item in all_terms["items"]] == [False, False]

    pin(client, "aws", True)
    filtered = client.get("/api/keywords", params={"min_count": 1, "pinned": True}).json()
    assert filtered["total"] == 1
    assert [item["key"] for item in filtered["items"]] == ["aws"]
    assert filtered["items"][0]["pinned"] is True
    # 概览也给出常驻词数。
    assert client.get("/api/keywords/overview").json()["pinned"] == 1

    pin(client, "AWS", False)
    assert client.get("/api/keywords", params={"min_count": 1}).json()["total"] == 2
    assert client.get("/api/keywords/overview").json()["pinned"] == 0


@respx.mock
def test_merge_moves_pin_to_target_and_undo_restores_it(client: TestClient) -> None:
    """常驻标记跟着写法走：合并搬到目标词，撤销还给原写法。"""

    fake = KeywordProvider(
        mapping={"计算平台": (Keyword("AWS", KeywordKind.ENTITY),)},
        default=(Keyword("云服务", KeywordKind.ENTITY),),
    )
    respx.get("https://pin-merge.test/feed").mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    source_id = create_source(client, "https://pin-merge.test/feed")
    disable_enrich(client, source_id)
    stub_providers(client, fake)
    collect(client, source_id)
    enrich_one(client, message_id(client, "计算平台发布"))
    enrich_one(client, message_id(client, "存储芯片涨价"))

    pin(client, "云服务", True)
    merged = client.post(
        "/api/keywords/merge", json={"keys": ["云服务"], "target": "AWS", "dry_run": False}
    )
    assert merged.status_code == 200, merged.text

    after = client.get("/api/keywords", params={"min_count": 1, "pinned": True}).json()
    assert [item["key"] for item in after["items"]] == ["aws"]

    undone = client.post(f"/api/keywords/merges/{merged.json()['record']['id']}/undo")
    assert undone.status_code == 200, undone.text
    restored = client.get("/api/keywords", params={"min_count": 1, "pinned": True}).json()
    # 标记回到原来的写法，且不会留在目标词上（合并前 AWS 并没有被标记）。
    assert [item["key"] for item in restored["items"]] == ["云服务"]
