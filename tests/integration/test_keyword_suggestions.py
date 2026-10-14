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

from rss_v2.domain import (
    ExternalServiceError,
    Keyword,
    KeywordKind,
    SuggestedGroup,
    SuggestResult,
)
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
        self.error: str | None = None  # 设置后调用直接失败，用于验证任务重试

    def suggest_merges(
        self,
        provider: Any,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[SuggestResult, dict[str, int], int]:
        self.suggestion_calls += 1
        if self.error:
            raise ExternalServiceError(self.error, "模拟失败")
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


def enqueue(client: TestClient, count: int = 1, size: int | None = None) -> dict[str, Any]:
    """排批次任务：每批一个后台任务，由 worker 执行。"""

    response = client.post("/api/keywords/suggestions", json={"count": count, "size": size})
    assert response.status_code == 202, response.text
    return dict(response.json())


def suggestions(client: TestClient, status: str = "pending") -> list[dict[str, Any]]:
    response = client.get("/api/keywords/suggestions", params={"status": status})
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def state(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/keywords/suggestions", params={"status": "pending"})
    assert response.status_code == 200, response.text
    return dict(response.json())


def suggestion_task(client: TestClient) -> dict[str, Any]:
    """任务表里最近一条合并建议任务，用于断言任务页能看到的东西。"""

    response = client.get("/api/tasks", params={"task_type": "keyword_merge_suggest", "limit": "5"})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "没有找到合并建议任务"
    return dict(items[0])


@respx.mock
def test_task_runs_through_worker_and_marks_out_of_batch_groups(client: TestClient) -> None:
    """入队 → worker 执行 → 建议落库；模型给出的越界写法被丢弃并计数。"""

    fake = SuggestProvider([("AWS", "云服务"), ("不存在的写法", "另一个不存在的写法")])
    seeded(client, fake, "https://suggest.test/feed")
    assert state(client) == {
        "items": [],
        "pending": 0,
        "queued": 0,
        "running": 0,
        "failed": 0,
        "total": 2,
        "scanned": 0,
    }

    receipt = enqueue(client)
    assert receipt["enqueued"] == 1 and receipt["reused"] == 0
    assert (receipt["queued"], receipt["running"]) == (1, 0)
    assert (receipt["total"], receipt["scanned"]) == (2, 0)
    task = suggestion_task(client)
    assert task["status"] == "queued"
    assert task["task_type"] == "keyword_merge_suggest"

    drain_worker(client)
    assert fake.suggestion_calls == 1
    prompt = fake.suggestion_prompts[0].user
    # 提示词里是本批词表：写法加类型，不带频次。
    assert "- AWS（实体）" in prompt
    assert "- 云服务（实体）" in prompt

    items = suggestions(client)
    assert len(items) == 1
    assert items[0]["target_key"] == "aws"
    assert [item["key"] for item in items[0]["members"]] == ["aws", "云服务"]
    assert suggestion_task(client)["status"] == "succeeded"
    after = state(client)
    assert (after["queued"], after["pending"], after["scanned"]) == (0, 1, 2)


@respx.mock
def test_enqueue_is_idempotent_and_reports_scan_progress(client: TestClient) -> None:
    """同一批只排一次、只调用一次模型；扫描进度按批次台账累计。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-idempotent.test/feed")

    first = enqueue(client, count=5, size=2)
    assert first["enqueued"] == 1  # 词表只有一批，多要也排不出来
    drain_worker(client)
    assert fake.suggestion_calls == 1

    again = enqueue(client, count=5, size=2)
    # 这一批已经排过（任务还在）：既不再排任务，也不重复调用模型。
    assert again["enqueued"] == 0 and again["reused"] + again["done"] == 1
    drain_worker(client)
    assert fake.suggestion_calls == 1
    assert state(client)["scanned"] == 2


@respx.mock
def test_failed_task_stays_visible_and_retries(client: TestClient) -> None:
    """失败走任务表：错误分类留在任务上，可重试的失败回到队列等待重试。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-fail.test/feed")
    fake.error = "rate_limited"

    enqueue(client)
    drain_worker(client)
    task = suggestion_task(client)
    assert task["status"] == "queued"  # 临时错误：留在队列里等重试
    assert task["error_code"] == "rate_limited"
    assert task["attempts"] == 1
    assert suggestions(client) == []

    # 冷却过后重试成功：把任务与 Key 的冷却都拨回可用，模拟等待期结束。
    container = client.app.state.container
    with container.database.transaction() as connection:
        connection.execute(
            "UPDATE tasks SET available_at=0 WHERE task_type='keyword_merge_suggest'"
        )
        connection.execute("UPDATE llm_provider_keys SET cooldown_until=NULL")
    fake.error = None
    drain_worker(client)
    assert suggestion_task(client)["status"] == "succeeded"
    assert len(suggestions(client)) == 1


@respx.mock
def test_dismissed_group_is_not_suggested_again(client: TestClient) -> None:
    """忽略后建议不再出现在待处理列表，而且不会再排一批重新问一次。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-dismiss.test/feed")
    enqueue(client)
    drain_worker(client)
    suggestion = suggestions(client)[0]

    dismissed = client.post(f"/api/keywords/suggestions/{suggestion['id']}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["pending"] == 0
    assert suggestions(client) == []

    # 重复处理被拒绝；重复入队命中已生成批次，不再调用模型。
    assert client.post(f"/api/keywords/suggestions/{suggestion['id']}/dismiss").status_code == 400
    replay = enqueue(client)
    assert replay["enqueued"] == 0 and replay["reused"] + replay["done"] == 1
    assert fake.suggestion_calls == 1


@respx.mock
def test_accept_records_the_merge_record(client: TestClient) -> None:
    """采纳：先用既有合并端点完成合并，再回填 merge_id 标记建议。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-accept.test/feed")
    enqueue(client)
    drain_worker(client)
    suggestion = suggestions(client)[0]

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

    listed = suggestions(client, status="accepted")
    assert [item["id"] for item in listed] == [suggestion["id"]]
    assert listed[0]["merge_id"] == merge_id
    assert listed[0]["resolved_at"] is not None
    missing = client.post(
        f"/api/keywords/suggestions/{suggestion['id']}/accept", json={"merge_id": "nope"}
    )
    assert missing.status_code == 400
