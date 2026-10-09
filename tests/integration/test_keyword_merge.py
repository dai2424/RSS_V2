"""人工合并与撤销的集成测试：词表并拢、检索与相关消息同步生效。"""

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import (
    KeywordProvider,
    collect,
    configure_model,
    create_source,
    enrich_all,
    feed,
    search,
)

from rss_v2.domain import Keyword, KeywordKind


def provider() -> KeywordProvider:
    """三条消息三种写法：AWS 与 谷歌云同指但字面不同，第三条提供合并的目标词 云服务。"""

    return KeywordProvider(
        mapping={
            "云服务": (Keyword("AWS", KeywordKind.ENTITY),),
            "云厂商": (Keyword("谷歌云", KeywordKind.ENTITY),),
        },
        default=(
            Keyword("OpenAI", KeywordKind.ENTITY),
            Keyword("云服务", KeywordKind.ENTITY),
        ),
    )


FEED = [
    ("aws", "云服务 降价", "Thu, 08 Oct 2026 00:00:00 GMT"),
    ("gcp", "云厂商 扩容", "Wed, 07 Oct 2026 00:00:00 GMT"),
    ("openai", "OpenAI 定价", "Tue, 06 Oct 2026 00:00:00 GMT"),
]


def seeded(client: TestClient, url: str = "https://merge.test/feed") -> str:
    """造三条已加工的消息，返回来源 id。"""

    respx.get(url).mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    source_id = create_source(client, url)
    collect(client, source_id)
    enrich_all(client, provider())
    return source_id


def merge(client: TestClient, keys: list[str], target: str, dry_run: bool = False):
    response = client.post(
        "/api/keywords/merge",
        json={"keys": keys, "target": target, "dry_run": dry_run},
    )
    assert response.status_code == 200, response.text
    return response.json()


@respx.mock
def test_merge_preview_matches_execution_and_unions_retrieval(client: TestClient) -> None:
    """合并前预览影响面，合并后两种写法互相命中，撤销后回到原状。"""

    seeded(client)
    assert len(search(client, keyword="AWS")) == 1
    assert len(search(client, keyword="谷歌云")) == 1

    preview = merge(client, ["谷歌云"], "AWS", dry_run=True)
    assert preview["record"] is None
    assert preview["preview"]["messages"] == 2
    assert preview["preview"]["mentions"] == 2
    assert [item["text"] for item in preview["preview"]["forms"]] == ["AWS", "谷歌云"]
    # 预览不落库。
    assert client.get("/api/keywords/merges").json() == []

    executed = merge(client, ["谷歌云"], "AWS")
    record = executed["record"]
    assert record["target_raw"] == "AWS"
    assert record["messages"] == 2
    assert executed["preview"]["messages"] == preview["preview"]["messages"]

    # 两种写法现在互相命中：并入的写法作为输入会解析到规范词。
    assert len(search(client, keyword="AWS")) == 2
    assert len(search(client, keyword="谷歌云")) == 2
    assert len(search(client, keyword="aws")) == 2
    # 无关的词不受影响。
    assert len(search(client, keyword="OpenAI")) == 1

    # 相关消息把两种写法视为同一线索。
    aws_message = search(client, keyword="AWS")[0]
    related = client.get(f"/api/messages/{aws_message['id']}/related").json()
    titles = [item["title"] for item in related]
    assert "云厂商 扩容" in titles
    shared = next(item for item in related if item["title"] == "云厂商 扩容")["shared"]
    # 共享词按"正在看的这条消息"的写法展示：合并后不显示成两个不同的词。
    assert [item["text"] for item in shared] == ["AWS"]

    undo = client.post(f"/api/keywords/merges/{record['id']}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["restored"] == 1
    assert len(search(client, keyword="AWS")) == 1
    assert len(search(client, keyword="谷歌云")) == 1
    history = client.get("/api/keywords/merges").json()
    assert history[0]["undone_at"] is not None
    # 撤销过的合并不能再撤一次。
    again = client.post(f"/api/keywords/merges/{record['id']}/undo")
    assert again.status_code == 400, again.text


@respx.mock
def test_chained_merge_resolves_to_final_term_and_unwinds_step_by_step(
    client: TestClient,
) -> None:
    """链式合并：A→B 之后把 B 并入 C，解析落到 C；撤销按逆序逐次还原。"""

    seeded(client)
    first = merge(client, ["谷歌云"], "AWS")["record"]
    second = merge(client, ["AWS"], "云服务")["record"]

    # 三种写法都解析到最终规范词：检索任一种都命中三条消息。
    for term in ("AWS", "谷歌云", "云服务"):
        assert len(search(client, keyword=term)) == 3, term

    assert client.post(f"/api/keywords/merges/{second['id']}/undo").status_code == 200
    # 退回第一次合并的状态：谷歌云仍并入 AWS，云服务只剩自己那条消息。
    assert len(search(client, keyword="AWS")) == 2
    assert len(search(client, keyword="谷歌云")) == 2
    assert len(search(client, keyword="云服务")) == 1

    assert client.post(f"/api/keywords/merges/{first['id']}/undo").status_code == 200
    assert len(search(client, keyword="AWS")) == 1
    assert len(search(client, keyword="谷歌云")) == 1


@respx.mock
def test_merge_rejects_unknown_target_and_same_target(client: TestClient) -> None:
    """目标必须已经在词表里；被合并词与目标相同要明确报错。"""

    seeded(client)
    unknown = client.post("/api/keywords/merge", json={"keys": ["AWS"], "target": "不存在的词"})
    assert unknown.status_code == 400, unknown.text
    assert unknown.json()["code"] == "keyword_target_unknown"

    same = client.post("/api/keywords/merge", json={"keys": ["AWS"], "target": "AWS"})
    assert same.status_code == 400, same.text
    assert same.json()["code"] == "keyword_merge_empty"

    not_found = client.post("/api/keywords/merges/no-such-merge/undo")
    assert not_found.status_code == 404, not_found.text
