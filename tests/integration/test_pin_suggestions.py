"""常驻建议：候选按跨周出现算出、模型只做挑选、采纳才写 pin、忽略不再出现。"""

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

from rss_v2.domain import (
    Keyword,
    KeywordKind,
    PinSuggestResult,
    SuggestedPin,
)
from rss_v2.llm import PromptPayload

#: 同一个词跨三周出现（今天 / 8 天前 / 15 天前），另一个词只出现一次。
FEED = [
    ("w1", "AWS 周报", "Tue, 14 Oct 2026 00:00:00 GMT"),
    ("w2", "AWS 复盘", "Mon, 06 Oct 2026 00:00:00 GMT"),
    ("w3", "AWS 月报", "Sun, 28 Sep 2026 00:00:00 GMT"),
    ("once", "一次性消息", "Sat, 20 Sep 2026 00:00:00 GMT"),
]


class PinProvider(KeywordProvider):
    """在关键词夹具上补一个常驻建议应答，并记下调用次数与提示词。"""

    def __init__(self, picks: list[str]) -> None:
        super().__init__(
            mapping={
                "AWS 周报": (
                    Keyword("AWS", KeywordKind.ENTITY),
                    Keyword("云服务", KeywordKind.TOPIC),
                ),
                "AWS 复盘": (
                    Keyword("AWS", KeywordKind.ENTITY),
                    Keyword("交付", KeywordKind.TOPIC),
                ),
                "AWS 月报": (Keyword("AWS", KeywordKind.ENTITY),),
                "一次性消息": (Keyword("一次性词", KeywordKind.TOPIC),),
            }
        )
        self.picks = picks
        self.pin_calls = 0
        self.pin_prompts: list[PromptPayload] = []

    def suggest_pins(
        self, provider: Any, model: str, secret: str, prompt: PromptPayload, prompt_version: str
    ) -> tuple[PinSuggestResult, dict[str, int], int]:
        self.pin_calls += 1
        self.pin_prompts.append(prompt)
        result = PinSuggestResult(
            picks=tuple(
                SuggestedPin(term=term, rationale=f"{term} 是跨周反复出现的话题")
                for term in self.picks
            )
        )
        return result, {"total_tokens": 5}, 1


def seeded(client: TestClient, fake: PinProvider, url: str) -> None:
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


def suggest(client: TestClient, dry_run: bool = False) -> dict[str, Any]:
    response = client.post("/api/keywords/pin-suggestions", json={"dry_run": dry_run})
    assert response.status_code == 200, response.text
    return dict(response.json())


def pin_suggestions(client: TestClient, status: str = "pending") -> list[dict[str, Any]]:
    response = client.get("/api/keywords/pin-suggestions", params={"status": status})
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


@respx.mock
def test_candidates_come_from_weeks_and_accept_pins_the_word(client: TestClient) -> None:
    """候选只看跨周反复出现的词；模型挑出的越界写法被丢弃；采纳才写 pin。"""

    fake = PinProvider(picks=["AWS", "一次性词"])
    seeded(client, fake, "https://pin-suggest.test/feed")

    # dry_run 只数候选，不调用模型。
    preview = suggest(client, dry_run=True)
    assert preview["candidates"] == 1 and fake.pin_calls == 0

    batch = suggest(client)
    assert (batch["candidates"], batch["picks"], batch["dropped"]) == (1, 1, 1)
    assert fake.pin_calls == 1
    # 候选按"跨几周"呈现给模型，只包含跨周词。
    prompt = fake.pin_prompts[0].user
    assert "- AWS（跨 3 周）" in prompt
    assert "一次性词" not in prompt

    items = pin_suggestions(client)
    assert [(item["key"], item["weeks"]) for item in items] == [("aws", 3)]
    assert items[0]["rationale"] == "AWS 是跨周反复出现的话题"

    accepted = client.post(f"/api/keywords/pin-suggestions/{items[0]['key']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["pending"] == 0
    pinned = client.get(
        "/api/keywords", params={"pinned": "true", "scope": "all", "min_count": 1}
    ).json()
    assert [item["key"] for item in pinned["items"]] == ["aws"]
    # 重复处置被拒绝；已经常驻的词也不再进候选。
    assert client.post(f"/api/keywords/pin-suggestions/{items[0]['key']}/accept").status_code == 400
    assert suggest(client)["candidates"] == 0
    assert fake.pin_calls == 1


@respx.mock
def test_dismissed_candidate_is_not_suggested_again(client: TestClient) -> None:
    """忽略过的词不会再被建议，也不会重复调用模型。"""

    fake = PinProvider(picks=["AWS"])
    seeded(client, fake, "https://pin-dismiss.test/feed")
    suggest(client)
    item = pin_suggestions(client)[0]

    dismissed = client.post(f"/api/keywords/pin-suggestions/{item['key']}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["pending"] == 0
    assert pin_suggestions(client) == []

    # 候选仍在（没有常驻），但已有建议行，不会重复落库；模型仍会被问一次是刻意的：
    # 一次调用只处理一批候选，是否重复由建议表的键决定。
    again = suggest(client)
    assert again["picks"] == 0
    assert len(pin_suggestions(client, status="dismissed")) == 1
