"""合并建议：批次幂等、越界写入被丢弃、忽略后不再出现、采纳只做标记。"""

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
    drain_worker,
    feed,
    search,
    stub_providers,
)

from rss_v2.domain import Keyword, KeywordKind, SuggestedGroup, SuggestResult
from rss_v2.llm import PromptPayload

FEED = [
    ("platform", "计算平台发布", "Sat, 10 Oct 2026 00:00:00 GMT"),
    ("chip", "存储芯片涨价", "Fri, 09 Oct 2026 00:00:00 GMT"),
]


class SuggestProvider(KeywordProvider):
    """在关键词夹具上补一个合并建议应答，并记下调用次数。"""

    def __init__(self, groups: list[tuple[str, ...]]) -> None:
        super().__init__(
            mapping={"计算平台": (Keyword("AWS", KeywordKind.ENTITY),)},
            default=(Keyword("云服务", KeywordKind.ENTITY),),
        )
        self.groups = groups
        self.suggestion_calls = 0
        self.suggestion_prompts: list[PromptPayload] = []

    def suggest_merges(
        self,
        provider: Any,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[SuggestResult, dict[str, int], int]:
        self.suggestion_calls += 1
        self.suggestion_prompts.append(prompt)
        result = SuggestResult(
            groups=tuple(
                SuggestedGroup(members=members, target=members[0], rationale="同一事物")
                for members in self.groups
            )
        )
        return result, {"total_tokens": 5}, 1


def seeded(client: TestClient, fake: SuggestProvider, url: str) -> None:
    """造两条消息：第一条产出 AWS，第二条产出云服务，二者可被建议合并。"""

    respx.get(url).mock(return_value=Response(200, content=feed(FEED)))
    configure_model(client)
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    stub_providers(client, fake)
    collect(client, source_id)
    for item in search(client, limit="50"):
        created = client.post(f"/api/messages/{item['id']}/enrich")
        assert created.status_code in (201, 202), created.text
    drain_worker(client)


def generate(client: TestClient, offset: int = 0, size: int = 60) -> dict[str, Any]:
    response = client.post("/api/keywords/suggestions", json={"offset": offset, "size": size})
    assert response.status_code == 200, response.text
    return dict(response.json())


def pending(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/keywords/suggestions", params={"status": "pending"})
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


@respx.mock
def test_batch_is_idempotent_and_marks_out_of_batch_groups(client: TestClient) -> None:
    """同一批只调用一次模型；模型给出的越界写法被丢弃并计数。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest.test/feed")
    fake.groups = [("AWS", "云服务"), ("不存在的写法", "另一个不存在的写法")]

    batch = generate(client)
    assert batch["groups"] == 1
    assert batch["dropped"] == 1
    assert batch["pending"] == 1
    assert fake.suggestion_calls == 1
    # 提示词里是本批词表：写法加类型，不带频次。
    prompt = fake.suggestion_prompts[0].user
    assert "- AWS（实体）" in prompt
    assert "- 云服务（实体）" in prompt

    # 同一批再来一次：直接回放，不再调用模型。
    again = generate(client)
    assert again["groups"] == 1 and fake.suggestion_calls == 1

    items = pending(client)
    assert len(items) == 1
    assert items[0]["target_key"] == "aws"
    assert [item["key"] for item in items[0]["members"]] == ["aws", "云服务"]
    assert items[0]["rationale"] == "同一事物"
    assert client.get("/api/keywords/overview").json()["suggestions_pending"] == 1


@respx.mock
def test_dismissed_group_is_not_suggested_again(client: TestClient) -> None:
    """忽略后建议不再出现在待处理列表，而且不会被再次建议。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-dismiss.test/feed")
    generate(client)
    suggestion = pending(client)[0]

    dismissed = client.post(f"/api/keywords/suggestions/{suggestion['id']}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["pending"] == 0
    assert pending(client) == []
    assert client.get("/api/keywords/overview").json()["suggestions_pending"] == 0

    # 重复处理与重复生成都被明确拒绝或跳过。
    again = client.post(f"/api/keywords/suggestions/{suggestion['id']}/dismiss")
    assert again.status_code == 400
    assert generate(client)["groups"] == 1
    assert fake.suggestion_calls == 1


@respx.mock
def test_accept_records_the_merge_record(client: TestClient) -> None:
    """采纳：先用既有合并端点完成合并，再回填 merge_id 标记建议。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-accept.test/feed")
    generate(client)
    suggestion = pending(client)[0]

    merged = client.post(
        "/api/keywords/merge", json={"keys": ["云服务"], "target": "AWS", "dry_run": False}
    )
    assert merged.status_code == 200, merged.text
    merge_id = merged.json()["record"]["id"]

    accepted = client.post(
        f"/api/keywords/suggestions/{suggestion['id']}/accept", json={"merge_id": merge_id}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["pending"] == 0

    listed = client.get("/api/keywords/suggestions", params={"status": "accepted"}).json()["items"]
    assert [item["id"] for item in listed] == [suggestion["id"]]
    assert listed[0]["merge_id"] == merge_id
    assert listed[0]["resolved_at"] is not None
    # 未知合并记录不能挂到建议上，避免记录指向不存在的东西。
    missing = client.post(
        f"/api/keywords/suggestions/{suggestion['id']}/accept", json={"merge_id": "nope"}
    )
    assert missing.status_code == 400
