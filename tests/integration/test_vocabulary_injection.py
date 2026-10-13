"""词表注入与复用判定：注入内容进提示词、复用按注入集合硬判定、重建与合并不破坏口径。"""

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

from rss_v2.domain import Keyword, KeywordKind

#: 按发布时间从新到旧；测试按"计算平台 → 存储芯片 → 电池 → 存储设备"的顺序加工。
FEED = [
    ("platform", "计算平台发布", "Sat, 10 Oct 2026 00:00:00 GMT"),
    ("chip", "存储芯片涨价", "Fri, 09 Oct 2026 00:00:00 GMT"),
    ("battery", "电池技术进展", "Thu, 08 Oct 2026 00:00:00 GMT"),
    ("storage", "存储设备降价", "Wed, 07 Oct 2026 00:00:00 GMT"),
]


def provider() -> KeywordProvider:
    return KeywordProvider(
        mapping={
            "计算平台": (
                Keyword("AWS", KeywordKind.ENTITY),
                Keyword("发布", KeywordKind.EVENT),
            ),
            "存储芯片": (Keyword("云服务", KeywordKind.ENTITY),),
        },
        default=(Keyword("其它", KeywordKind.TOPIC),),
    )


def seeded(
    client: TestClient, fake: KeywordProvider, url: str = "https://inject.test/feed"
) -> None:
    """造三条待加工的消息：关掉自动加工，改成按顺序手动入队。"""

    respx.get(url).mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    stub_providers(client, fake)
    collect(client, source_id)


def message_id(client: TestClient, title: str) -> str:
    """按标题取消息 id；加工顺序决定谁先成为词表。"""

    for item in search(client, limit="50"):
        if item["latest_version"]["title"] == title:
            return str(item["id"])
    raise AssertionError(f"没有找到标题为 {title} 的消息")


def enrich_one(client: TestClient, message_id: str) -> None:
    created = client.post(f"/api/messages/{message_id}/enrich")
    assert created.status_code == 202, created.text
    drain_worker(client)


def keywords_of(client: TestClient, message_id: str) -> list[dict[str, object]]:
    detail = client.get(f"/api/messages/{message_id}").json()
    return list(detail["versions"][0]["enrichments"][0]["keywords"])


def overview(client: TestClient) -> dict[str, object]:
    response = client.get("/api/keywords/overview")
    assert response.status_code == 200, response.text
    return dict(response.json())


def stored_revisions(client: TestClient) -> list[int]:
    """直接读加工结果行上的词表版本，验证它确实落了库。"""

    connection = client.app.state.container.database.connect()
    try:
        rows = connection.execute(
            "SELECT vocab_revision FROM message_enrichments WHERE vocab_revision IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    return sorted(int(row["vocab_revision"]) for row in rows)


@respx.mock
def test_first_run_has_no_vocabulary_then_second_sees_it(client: TestClient) -> None:
    """首次加工词表为空，第二次加工能看到前一条产出的写法并按词表判定复用。"""

    fake = provider()
    seeded(client, fake)
    platform = message_id(client, "计算平台发布")
    chip = message_id(client, "存储芯片涨价")

    enrich_one(client, platform)
    assert "（当前词表为空" in fake.prompts[-1].user
    assert [item["reused"] for item in keywords_of(client, platform)] == [None, None]

    enrich_one(client, chip)
    prompt = fake.prompts[-1].user
    # 注入的是展示写法、按类型分组，并出现在正文之前。
    assert "实体：AWS" in prompt
    assert "事件：发布" in prompt
    assert prompt.index("实体：AWS") < prompt.index("标题：")

    assert keywords_of(client, chip) == [
        {"text": "云服务", "kind": "entity", "reused": False},
    ]
    overview = client.get("/api/keywords/overview").json()
    assert (overview["reused"], overview["reuse_judged"]) == (0, 1)
    assert overview["reuse_rate"] == 0.0


@respx.mock
def test_reuse_is_judged_by_matching_key(client: TestClient) -> None:
    """写法不同但匹配键相同算复用：大小写与空格差异不该算成新建。"""

    fake = KeywordProvider(
        mapping={
            "计算平台": (Keyword("AWS", KeywordKind.ENTITY),),
            "存储芯片": (Keyword("aws", KeywordKind.ENTITY),),
        },
        default=(Keyword("其它", KeywordKind.TOPIC),),
    )
    seeded(client, fake)
    enrich_one(client, message_id(client, "计算平台发布"))
    enrich_one(client, message_id(client, "存储芯片涨价"))

    assert keywords_of(client, message_id(client, "存储芯片涨价")) == [
        {"text": "aws", "kind": "entity", "reused": True},
    ]


@respx.mock
def test_rebuild_index_keeps_reuse_flags(client: TestClient) -> None:
    """重建索引从 JSON 快照反解，但服务端的复用判定必须保留下来。"""

    fake = provider()
    seeded(client, fake)
    enrich_one(client, message_id(client, "计算平台发布"))
    chip = message_id(client, "存储芯片涨价")
    enrich_one(client, chip)
    before = keywords_of(client, chip)

    rebuilt = client.post("/api/keywords/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    assert keywords_of(client, chip) == before


@respx.mock
def test_vocabulary_revision_advances_only_when_injection_changes(client: TestClient) -> None:
    """词表版本只在注入内容变化时推进：出现新词 +1，注入内容不变则不动。"""

    fake = KeywordProvider(
        mapping={
            "计算平台": (Keyword("AWS", KeywordKind.ENTITY),),
            "存储芯片": (Keyword("AWS", KeywordKind.ENTITY),),
        },
        default=(Keyword("AWS", KeywordKind.ENTITY),),
    )
    seeded(client, fake)
    # 第一条：词表为空 → 版本 1。
    enrich_one(client, message_id(client, "计算平台发布"))
    assert overview(client)["vocab_revision"] == 1
    # 第二条：注入内容多了 AWS → 推进到 2。
    enrich_one(client, message_id(client, "存储芯片涨价"))
    assert overview(client)["vocab_revision"] == 2
    # 第三条：三条消息产出同一个词，注入内容与上次完全相同 → 不推进。
    enrich_one(client, message_id(client, "电池技术进展"))
    assert overview(client)["vocab_revision"] == 2
    assert stored_revisions(client) == [1, 2, 2]


@respx.mock
def test_merged_writing_leaves_the_injected_vocabulary(client: TestClient) -> None:
    """被合并掉的写法不再进注入词表，检索两种写法都能命中同一个词条。"""

    fake = provider()
    seeded(client, fake, url="https://merge-inject.test/feed")
    enrich_one(client, message_id(client, "计算平台发布"))
    enrich_one(client, message_id(client, "存储芯片涨价"))
    assert overview(client)["vocab_revision"] == 2

    # 第三条加工时，云服务已经进了词表。
    enrich_one(client, message_id(client, "电池技术进展"))
    assert "云服务" in fake.prompts[-1].user
    assert overview(client)["vocab_revision"] == 3

    merged = client.post(
        "/api/keywords/merge", json={"keys": ["云服务"], "target": "AWS", "dry_run": False}
    )
    assert merged.status_code == 200, merged.text

    # 下一条消息加工时，注入词表里只剩规范写法，被合并掉的写法不再出现。
    enrich_one(client, message_id(client, "存储设备降价"))
    prompt = fake.prompts[-1].user
    assert "实体：AWS" in prompt
    assert "云服务" not in prompt
    # 合并改变了注入内容，词表版本随之推进。
    assert overview(client)["vocab_revision"] == 4
    # 两种写法检索到同一个词条：被合并的写法仍然可检索，只是不再注入。
    assert len(search(client, keyword="AWS")) == len(search(client, keyword="云服务")) == 2
