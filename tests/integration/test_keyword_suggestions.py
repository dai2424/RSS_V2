"""合并建议：批次幂等、越界写入被丢弃、忽略后不再出现、采纳只做标记。"""

import time
from typing import Any

import pytest
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
    DomainError,
    ExternalServiceError,
    Keyword,
    KeywordKind,
    SuggestedGroup,
    SuggestResult,
    TaskStatus,
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
    before = state(client)
    assert before["total"] == 2
    assert (before["scanned"], before["last_scanned_at"]) == (0, None)
    assert (before["pending"], before["queued"], before["running"], before["failed"]) == (
        0,
        0,
        0,
        0,
    )

    receipt = enqueue(client)
    assert receipt["enqueued"] == 1 and receipt["queued_words"] == 2
    assert (receipt["queued"], receipt["running"]) == (1, 0)
    assert receipt["total"] == 2
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
    assert after["last_scanned_at"] is not None


@respx.mock
def test_enqueue_is_idempotent_and_reports_scan_progress(client: TestClient) -> None:
    """同一批只排一次、只调用一次模型；扫描进度按批次台账累计。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-idempotent.test/feed")

    first = enqueue(client, count=5, size=2)
    assert first["enqueued"] == 1  # 词表只有一批，多要也排不出来

    # 连点两次：第二次命中的是已经排过的批次，不重复入队，也就不会重复付费。
    again = enqueue(client, count=5, size=2)
    assert again["enqueued"] == 0 and again["queued"] == 1

    drain_worker(client)
    assert fake.suggestion_calls == 1

    # 生成过之后再排：滚动重扫会排出新的一批（老词换个搭档重看）——这是刻意的，
    # 合并是同指判断，一次看过不等于以后不用再看。
    after = enqueue(client, count=5, size=2)
    assert after["enqueued"] == 1
    assert state(client)["scanned"] == 2


@respx.mock
def test_scan_is_rolling_after_the_vocabulary_changes(client: TestClient) -> None:
    """扫描是滚动的：新词与最久没看过的老词混排，换序不会让整表重扫。

    这是长期运行的关键：合并是同指判断，新写法要和老写法放在一起才判断得出；而词表
    按频次与最近出现排序，新词会插到前面把旧词挤位——若按位置记账，挤位会让整张表
    看起来"没扫过"，于是每次都要从头再付一遍费用。
    """

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-inc-1.test/feed")
    enqueue(client)
    drain_worker(client)
    assert state(client)["scanned"] == 2
    assert fake.suggestion_calls == 1

    # 第二个来源带来一条新消息与新词：它的最近出现更新，因此排在词表更前面。
    fake.mapping["新话题出现"] = (Keyword("新话题", KeywordKind.TOPIC),)
    respx.get("https://suggest-inc-2.test/feed").mock(
        return_value=Response(
            200, content=feed([("fresh", "新话题出现", "Sun, 11 Oct 2026 00:00:00 GMT")])
        )
    )
    second = create_source(client, "https://suggest-inc-2.test/feed")
    collect(client, second)
    drain_worker(client)
    after = state(client)
    assert (after["total"], after["scanned"]) == (3, 2)

    # 下一批是新旧混排：新词与"最久没看过"的老词同批出现——同指判断需要它们碰面，
    # 而且词表与写法会变，看过的词也要轮着重扫。
    enqueue(client)
    drain_worker(client)
    prompt = fake.suggestion_prompts[-1].user
    assert "- 新话题（主题）" in prompt
    assert "- AWS（实体）" in prompt or "- 云服务（实体）" in prompt
    assert state(client)["scanned"] == 3


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
    # 滚动重扫会再排一批，但已忽略的组不会再出现（组指纹去重）。
    replay = enqueue(client)
    assert replay["enqueued"] == 1
    drain_worker(client)
    assert suggestions(client) == []


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


@respx.mock
def test_each_round_enqueues_a_new_batch(client: TestClient) -> None:
    """连续三轮都要排到新批次。

    扫描时间不刷新时，队列顺序永远不变、每轮算出同样的指纹，于是第二轮被当成"已排过"、
    第三轮起干脆排不出批次——这条用例盯的就是那个回归。
    """

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://rounds.test/feed")
    # 再加一个来源把词表撑到四词，好让每批 2 个词能排出多轮。
    respx.get("https://rounds-2.test/feed").mock(
        return_value=Response(
            200,
            content=feed(
                [
                    ("c1", "存储芯片涨价", "Sat, 10 Oct 2026 00:00:00 GMT"),
                    ("c2", "电池技术进展", "Fri, 09 Oct 2026 00:00:00 GMT"),
                ]
            ),
        )
    )
    second = create_source(client, "https://rounds-2.test/feed")
    collect(client, second)
    drain_worker(client)

    for round_no in range(1, 4):
        # 指纹里带"上次看过的时间"（秒级）：间隔不到一秒的两轮本来就算同一轮，
        # 这里等一下，模拟"隔一段时间再扫一轮"。
        if round_no > 1:
            time.sleep(1.1)
        receipt = enqueue(client, count=1, size=2)
        assert receipt["enqueued"] == 1, f"第 {round_no} 轮没有排到批次"
        drain_worker(client)
        assert fake.suggestion_calls == round_no, f"第 {round_no} 轮没有真正调用模型"


@respx.mock
def test_same_group_is_not_suggested_twice(client: TestClient) -> None:
    """同一组写法只建议一次；忽略过的组再扫到也不会回来（成员指纹去重）。"""

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://dedupe.test/feed")
    enqueue(client)
    drain_worker(client)
    item = suggestions(client)[0]

    dismissed = client.post(f"/api/keywords/suggestions/{item['id']}/dismiss")
    assert dismissed.status_code == 200, dismissed.text

    # 再扫一轮：模型仍返回同一组，但成员指纹已存在，不再落库。
    receipt = enqueue(client)
    assert receipt["enqueued"] == 1
    drain_worker(client)
    assert suggestions(client) == []
    listed = client.get("/api/keywords/suggestions", params={"status": "dismissed"}).json()["items"]
    assert [row["id"] for row in listed] == [item["id"]]


@respx.mock
def test_expired_worker_cannot_store_suggestions(client: TestClient) -> None:
    """租约被回收后，过期 worker 的模型结果既不进建议表也不进批次台账。

    台账是关键：写进去就等于"这批词已经看过"，接手的 worker 会跳过它们，那批调用白花。
    """

    fake = SuggestProvider([("AWS", "云服务")])
    seeded(client, fake, "https://suggest-lease.test/feed")
    assert enqueue(client)["enqueued"] == 1
    container = client.app.state.container
    expired = container.tasks.claim_next(["keyword_merge_suggest"], 100, 20)
    assert expired is not None
    container.tasks.reclaim_expired(121)
    current = container.tasks.claim_next(["keyword_merge_suggest"], 121, 20)
    assert current is not None and current.lease_token != expired.lease_token

    with pytest.raises(DomainError, match="租约"):
        container.suggestion_service.run(expired)
    assert fake.suggestion_calls == 1  # 模型确实调用了，只是结果被丢弃
    assert suggestions(client) == []
    assert container.tasks.get(expired.id).status == TaskStatus.RUNNING

    # 接手的 worker 重新调用模型并落库：批次没有被过期结果提前记账。
    batch = container.suggestion_service.run(current)
    assert (batch.groups, fake.suggestion_calls) == (1, 2)
    assert len(suggestions(client)) == 1
    assert container.tasks.get(current.id).status == TaskStatus.SUCCEEDED
