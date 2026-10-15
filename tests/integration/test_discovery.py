"""选题发现集成测试：范围筛选、事件聚合、越界过滤、幂等、选题建项目与租约。

用固定假模型（按提示词里的消息 ID 分组）验证服务端的确定性规则；模型本身的能力
不在测试范围内——测试要证明的是"模型给什么都不会让服务端写出越界或重复的候选"。
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import (
    CATEGORY_ID,
    collect,
    configure_model,
    create_source,
    disable_enrich,
    feed,
)
from pydantic import ValidationError

from rss_v2.domain import DomainError, ExternalServiceError, Provider
from rss_v2.llm import PromptPayload
from rss_v2.services.discovery import DiscoveryLimits
from rss_v2.tasks.worker import Worker

MESSAGE_ID = re.compile(r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]")

#: 假模型的默认分组规则：同一批里前两条算一个事件，其余每条各自成一个候选。
DEFAULT_PLAN = [[0, 1], [2], [3], [4]]


class ScriptedProvider:
    """按提示词里出现的消息 ID 返回预设分组。

    `plan` 里每个子列表是本批内的下标分组；`raw` 直接给原始 JSON 文本（测非法输出）；
    `error` 用于模拟上游失败；`truncate_over` 模拟"消息太多时输出被截断"（真实运行出现过）。
    返回值仍经过传入的 schema 校验，与真实适配器一致。
    """

    def __init__(
        self,
        plan: list[list[int]] | None = None,
        raw: str | None = None,
        error: ExternalServiceError | None = None,
        truncate_over: int | None = None,
    ) -> None:
        self.plan = plan or DEFAULT_PLAN
        self.raw = raw
        self.error = error
        self.truncate_over = truncate_over
        self.calls = 0
        self.prompts: list[PromptPayload] = []

    def structured(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
        task_kind: str,
        schema: type[Any],
        max_output_tokens: int | None = None,
    ) -> tuple[Any, dict[str, int], int]:
        self.calls += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        members = MESSAGE_ID.findall(prompt.user)
        if self.truncate_over is not None and len(members) > self.truncate_over:
            raise ExternalServiceError(
                "llm_output_truncated", "模型回答被输出长度上限截断，内容不完整"
            )
        if self.raw is not None:
            text = self.raw
        else:
            text = json.dumps(self._payload(MESSAGE_ID.findall(prompt.user)), ensure_ascii=False)
        try:
            digest = schema.model_validate_json(text)
        except ValidationError as exc:
            # 真实适配器把 schema 校验失败翻译成 llm_invalid_output，假模型保持一致。
            raise ExternalServiceError(
                "llm_invalid_output", f"结构校验失败：{exc.error_count()} 处"
            ) from exc
        return digest, {"total_tokens": 7}, 3

    def _payload(self, ids: list[str]) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for score, group in enumerate(self.plan, start=1):
            members = [ids[index] for index in group if index < len(ids)]
            if not members:
                continue
            candidates.append(
                {
                    "member_message_ids": members,
                    "title": f"事件 {score}",
                    "summary": f"第 {score} 个事件的概要。",
                    "score": 5 - score,
                    "reasons": [{"kind": "interesting", "text": f"理由 {score}"}],
                }
            )
        return {"candidates": candidates}


def _seed(client: TestClient, url: str = "https://discovery.test/feed") -> tuple[str, list[str]]:
    """采集三条消息（两条同一事件、一条独立）。

    返回 (来源 id, 消息 id 列表)，列表按发布时间升序排列——与发现输入的顺序一致。
    """

    items = [
        ("d1", "尊界 V800 刹车踏板支架连断三根", "Wed, 07 Oct 2026 02:41:00 GMT"),
        ("d2", "百万 MPV 尊界 V800 陷刹车踏板断裂争议", "Wed, 07 Oct 2026 03:02:00 GMT"),
        ("d3", "开源项目 DiPlay 让安卓车机运行 CarPlay", "Wed, 07 Oct 2026 12:55:00 GMT"),
    ]
    respx.get(url).mock(return_value=Response(200, content=feed(items)))
    source_id = create_source(client, url)
    disable_enrich(client, source_id)
    collect(client, source_id)
    listed = client.get("/api/messages", params={"limit": "50"}).json()["items"]
    ordered = sorted(listed, key=lambda item: int(item["latest_version"]["published_at"]))
    return source_id, [str(item["id"]) for item in ordered]


def _stub(client: TestClient, fake: ScriptedProvider) -> None:
    container = client.app.state.container
    container.discovery_service.provider = fake


def _messages(client: TestClient) -> list[dict[str, Any]]:
    return list(client.get("/api/messages", params={"limit": "50"}).json()["items"])


def _window(client: TestClient, source_id: str | None = None) -> tuple[int, int]:
    """覆盖当前消息的最小时段：按实际发布时间算，避免测试依赖具体日期。"""

    params: dict[str, str] = {"limit": "50"}
    if source_id:
        params["source_id"] = source_id
    items = client.get("/api/messages", params=params).json()["items"]
    times = [int(item["latest_version"]["published_at"]) for item in items]
    return min(times) - 60, max(times) + 60


def _create(client: TestClient, **scope: Any) -> dict[str, Any]:
    response = client.post("/api/discoveries", json=scope)
    assert response.status_code == 202, response.text
    return dict(response.json())


def _run(client: TestClient) -> None:
    Worker(client.app.state.container).run_once()


@respx.mock
def test_discovery_aggregates_event_and_locks_members(client: TestClient) -> None:
    """同一事件的多条消息聚成一个候选；成员锁定版本，理由与排序来自模型。"""

    configure_model(client)
    _, ids = _seed(client)
    fake = ScriptedProvider(plan=[[0, 1], [2]])
    _stub(client, fake)
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    assert batch["status"] == "queued"
    assert batch["task_id"]
    assert batch["message_count"] == 3
    _run(client)

    detail = client.get(f"/api/discoveries/{batch['id']}").json()
    assert detail["batch"]["status"] == "succeeded"
    assert detail["batch"]["candidate_count"] == 2
    assert [item["rank"] for item in detail["candidates"]] == [1, 2]

    first = detail["candidates"][0]
    assert first["title"] == "事件 1"
    assert first["reasons"] == [{"kind": "interesting", "text": "理由 1"}]
    assert first["score"] == 4
    assert first["member_count"] == 2
    assert {member["message_id"] for member in first["members"]} == set(ids[:2])
    # 事件时间取成员里最早的时间；线索标题等于候选标题（事件成员复用线索存储）。
    assert first["event_time"] == min(int(member["published_at"]) for member in first["members"])
    lead_id = first["lead_id"]
    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["title"] == "事件 1"
    assert len(lead["members"]) == 2


@respx.mock
def test_single_article_without_keywords_forms_candidate(client: TestClient) -> None:
    """单篇且没有加工结果的消息同样能成为候选（短快讯不因缺少关键词被漏掉）。"""

    configure_model(client)
    _, ids = _seed(client)
    # 这条消息没有加工结果：discovery 的输入不按关键词过滤。
    unenriched = ids[2]
    assert client.get(f"/api/messages/{unenriched}").json()["versions"][-1]["enrichments"] == []
    fake = ScriptedProvider(plan=[[2]])
    _stub(client, fake)
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    detail = client.get(f"/api/discoveries/{batch['id']}").json()
    assert detail["batch"]["candidate_count"] == 1
    assert [member["message_id"] for member in detail["candidates"][0]["members"]] == [unenriched]


@respx.mock
def test_out_of_range_and_duplicate_members_are_counted(client: TestClient) -> None:
    """模型给出不存在的消息或把同一消息塞进两个候选时丢弃并计数，不写进候选。"""

    configure_model(client)
    _, ids = _seed(client)
    raw = json.dumps(
        {
            "candidates": [
                {
                    "member_message_ids": [ids[0], "00000000-0000-4000-8000-000000000000"],
                    "title": "越界成员",
                    "summary": "含一条不存在的消息。",
                    "score": 4,
                    "reasons": [{"kind": "important", "text": "测试越界"}],
                },
                {
                    "member_message_ids": [ids[0]],
                    "title": "重复成员",
                    "summary": "与上一个候选争抢同一消息。",
                    "score": 3,
                    "reasons": [{"kind": "interesting", "text": "测试重复"}],
                },
            ]
        },
        ensure_ascii=False,
    )
    _stub(client, ScriptedProvider(raw=raw))
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    body = client.get(f"/api/discoveries/{batch['id']}").json()
    assert body["batch"]["status"] == "succeeded"
    assert body["batch"]["out_of_range"] == 1
    assert body["batch"]["duplicate_members"] == 1
    # 第二个候选被同一消息占走后没有成员，整条丢弃。
    assert body["batch"]["candidate_count"] == 1
    assert body["candidates"][0]["member_count"] == 1


@respx.mock
def test_batches_merge_candidates_sharing_members(client: TestClient) -> None:
    """分批执行时，跨批共享成员的两个候选合并成一个（并集、理由去重）。"""

    configure_model(client)
    _, ids = _seed(client)
    container = client.app.state.container
    container.discovery_service.limits = DiscoveryLimits(
        batch_size=2, max_window_messages=400, max_candidates=30
    )
    # 两批各出一个候选：第一批 {d1,d2}，第二批 {d2,d3}（相邻批重叠 d2）。
    fake = ScriptedProvider(plan=[[0, 1]])
    _stub(client, fake)
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    body = client.get(f"/api/discoveries/{batch['id']}").json()
    assert fake.calls == 2  # 3 条消息按批 2 切成两批
    # 跨批共享 d2 → 两个候选并成一个，成员取并集。
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["title"] == "事件 1"
    members = {member["message_id"] for member in body["candidates"][0]["members"]}
    assert members == set(ids)
    assert body["batch"]["duplicate_members"] == 0


@respx.mock
def test_truncated_output_splits_batch_and_still_succeeds(client: TestClient) -> None:
    """输出被截断时不放弃整次发现：把这一批对半拆开重试，结果仍然落库。"""

    configure_model(client)
    _, ids = _seed(client)
    # 假模型：一批超过 2 条就"写不完"，拆到 2 条以内才返回结果。
    fake = ScriptedProvider(plan=[[0, 1]], truncate_over=2)
    _stub(client, fake)
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    body = client.get(f"/api/discoveries/{batch['id']}").json()
    assert body["batch"]["status"] == "succeeded"
    assert body["batch"]["error_code"] is None
    assert fake.calls == 3  # 整批失败一次 + 拆出的两批各一次
    # 拆出的两批重叠一条消息，所以跨切点的事件仍然合成一个候选。
    assert len(body["candidates"]) == 1
    assert {m["message_id"] for m in body["candidates"][0]["members"]} == set(ids)
    assert body["batch"]["duplicate_members"] == 0


@respx.mock
def test_batches_cut_at_time_gaps_not_fixed_counts(client: TestClient) -> None:
    """批次数按时间间隔切：同一时间密集入的多条消息不会被切成碎片批。

    真实数据里同一分钟常有多条消息（间隔 0），早期的贪心实现会把它们切成"1 条一批"，
    调用次数翻数倍。
    """

    configure_model(client)
    items = [
        (f"g{index}", f"同刻消息 {index}", "Wed, 07 Oct 2026 02:41:00 GMT") for index in range(6)
    ] + [("later", "稍后的消息", "Wed, 07 Oct 2026 09:00:00 GMT")]
    respx.get("https://gaps.test/feed").mock(return_value=Response(200, content=feed(items)))
    source_id = create_source(client, "https://gaps.test/feed")
    disable_enrich(client, source_id)
    collect(client, source_id)
    container = client.app.state.container
    container.discovery_service.limits = DiscoveryLimits(batch_size=4)
    fake = ScriptedProvider(plan=[[0, 1]])
    _stub(client, fake)
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    assert fake.calls == 2  # 7 条按上限 4 切成两批，而不是七批碎片
    assert client.get(f"/api/discoveries/{batch['id']}").json()["batch"]["status"] == "succeeded"


@respx.mock
def test_window_bounds_and_source_filter(client: TestClient) -> None:
    """窗口边界按 UTC 秒闭开区间判定；来源筛选只把该来源的消息交给模型。"""

    configure_model(client)
    source_id, ids = _seed(client)
    times = sorted(int(item["latest_version"]["published_at"]) for item in _messages(client))
    first, second, third = times

    fake = ScriptedProvider(plan=[[0], [1], [2]])
    _stub(client, fake)
    # [first, second)：只含第一条消息。
    batch = _create(client, window_start=first, window_end=second)
    assert batch["message_count"] == 1
    _run(client)
    body = client.get(f"/api/discoveries/{batch['id']}").json()
    assert body["batch"]["candidate_count"] == 1
    assert [m["message_id"] for m in body["candidates"][0]["members"]] == [ids[0]]

    # 来源筛选：另一个来源不在窗口内。
    other = client.post(
        "/api/sources",
        json={
            "name": "别处",
            "url": "https://other.test/feed",
            "platform": "Other",
            "language": "auto",
            "category_id": CATEGORY_ID,
        },
    )
    assert other.status_code == 201
    filtered = _create(
        client,
        window_start=first,
        window_end=third + 60,
        source_id=source_id,
    )
    assert filtered["message_count"] == 3
    assert filtered["source_name"] == "Example Tech"


@respx.mock
def test_empty_window_succeeds_without_model_call(client: TestClient) -> None:
    """空窗口不是失败：直接完成、0 个候选，也不调用模型。"""

    configure_model(client)
    _seed(client)
    fake = ScriptedProvider()
    _stub(client, fake)
    start, end = _window(client)
    # 窗口完全在消息之前：没有任何输入。
    batch = _create(client, window_start=start - 10_000, window_end=start - 5_000)
    assert batch["status"] == "succeeded"
    assert batch["message_count"] == 0
    assert batch["candidate_count"] == 0
    assert batch["task_id"] is None
    assert fake.calls == 0
    assert client.get(f"/api/discoveries/{batch['id']}").json()["candidates"] == []


@respx.mock
def test_repeated_create_reuses_batch_and_failed_batch_can_retry(client: TestClient) -> None:
    """同一范围与输入不重复建批次；失败后再次发起在同一批次上新建任务。"""

    configure_model(client)
    _, _ = _seed(client)
    fake = ScriptedProvider()
    _stub(client, fake)
    start, end = _window(client)

    first = _create(client, window_start=start, window_end=end)
    assert first["reused"] is False
    again = _create(client, window_start=start, window_end=end)
    assert again["id"] == first["id"]
    assert again["reused"] is True
    tasks_before = client.get("/api/tasks", params={"task_type": "discover_events"}).json()
    assert tasks_before["total"] == 1

    # 模型失败 → 任务按冷却重试一次后仍失败（假 provider 一直失败）。
    _stub(client, ScriptedProvider(error=ExternalServiceError("llm_timeout", "上游超时")))
    _run(client)
    failed = client.get(f"/api/discoveries/{first['id']}").json()["batch"]
    assert failed["status"] == "failed"
    assert failed["error_code"] == "llm_timeout"
    assert failed["error_message"] == "上游超时"

    retried = _create(client, window_start=start, window_end=end)
    assert retried["id"] == first["id"]
    assert retried["reused"] is False
    assert retried["status"] == "queued"
    assert retried["task_id"] != first["task_id"]
    assert retried["error_code"] is None


@respx.mock
def test_expired_lease_blocks_late_write(client: TestClient) -> None:
    """租约失效后继续执行会报 task_lease_lost，批次状态不被晚到的 worker 改写。"""

    configure_model(client)
    _seed(client)
    container = client.app.state.container
    fake = ScriptedProvider()
    _stub(client, fake)
    start, end = _window(client)

    batch = _create(client, window_start=start, window_end=end)
    task = container.tasks.get(str(batch["task_id"]))
    assert task is not None
    claimed = container.tasks.claim_next(["discover_events"], int(task.created_at), 300)
    assert claimed is not None
    # 模拟租约过期被回收后再续租：token 已换人。
    container.tasks.reclaim_expired(int(claimed.lease_until or 0) + 1)

    with pytest.raises(DomainError) as raised:
        container.discovery_service.run(claimed)
    assert raised.value.code == "task_lease_lost"
    stored = client.get(f"/api/discoveries/{batch['id']}").json()["batch"]
    # 晚到的写入被拒绝：批次仍停在排队，任务也没有被标成完成。
    assert stored["status"] == "queued"
    task_row = container.discovery_service.tasks.get(claimed.id)
    assert task_row is not None and task_row.status == "queued"
    assert fake.calls == 0  # 租约校验发生在调用模型之前


@respx.mock
def test_select_candidate_creates_project_and_hides_discovery_leads(client: TestClient) -> None:
    """选择题建立写作项目（重复选择幂等）；自动线索不出现在人工线索列表。"""

    configure_model(client)
    _, _ = _seed(client)
    _stub(client, ScriptedProvider(plan=[[0, 1]]))
    start, end = _window(client)
    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    detail = client.get(f"/api/discoveries/{batch['id']}").json()
    candidate = detail["candidates"][0]
    assert candidate["project_id"] is None

    created = client.post(f"/api/discoveries/{batch['id']}/candidates/{candidate['id']}/select")
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["status"] == "materials_pending"
    assert project["lead_id"] == candidate["lead_id"]

    again = client.post(f"/api/discoveries/{batch['id']}/candidates/{candidate['id']}/select")
    assert again.status_code == 201
    assert again.json()["id"] == project["id"]
    assert again.json()["reused"] is True

    # 批次详情回填 project_id。
    refreshed = client.get(f"/api/discoveries/{batch['id']}").json()
    assert refreshed["candidates"][0]["project_id"] == project["id"]

    # 人工线索列表默认空；all 能看到发现建立的线索。
    manual = client.get("/api/leads").json()
    assert manual["total"] == 0
    all_leads = client.get("/api/leads", params={"kind": "all"}).json()
    assert all_leads["total"] == 1
    assert all_leads["items"][0]["id"] == candidate["lead_id"]

    # 写作项目详情：成员与计数（P2a 阶段各阶段产物为 0）。
    detail_project = client.get(f"/api/projects/{project['id']}").json()
    assert len(detail_project["members"]) == candidate["member_count"]
    assert detail_project["counts"] == {"materials": 0, "facts": 0, "outlines": 0, "articles": 0}
    assert client.get("/api/projects").json()["total"] == 1


@respx.mock
def test_delete_batch_removes_candidates_and_auto_leads(client: TestClient) -> None:
    """删除批次会清掉候选与无人引用的自动线索，人工线索不受影响。"""

    configure_model(client)
    _, ids = _seed(client)
    manual = client.post("/api/leads", json={"title": "人工线索", "message_ids": ids[:2]})
    assert manual.status_code == 201
    _stub(client, ScriptedProvider(plan=[[0, 1]]))
    start, end = _window(client)
    batch = _create(client, window_start=start, window_end=end)
    _run(client)
    detail = client.get(f"/api/discoveries/{batch['id']}").json()
    lead_id = detail["candidates"][0]["lead_id"]

    deleted = client.delete(f"/api/discoveries/{batch['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 1
    assert client.get(f"/api/discoveries/{batch['id']}").status_code == 404
    assert client.get(f"/api/leads/{lead_id}").status_code == 404
    assert client.get(f"/api/leads/{manual.json()['id']}").status_code == 200


@respx.mock
def test_window_too_large_and_scope_mismatch_are_rejected(client: TestClient) -> None:
    """超限窗口与互相矛盾的分类/来源组合在创建时就明确拒绝。"""

    configure_model(client)
    source_id, _ = _seed(client)
    container = client.app.state.container
    container.discovery_service.limits = DiscoveryLimits(max_window_messages=1)
    start, end = _window(client)
    too_large = client.post("/api/discoveries", json={"window_start": start, "window_end": end})
    assert too_large.status_code == 400
    assert too_large.json()["code"] == "discovery_window_too_large"
    assert "缩小时间范围" in too_large.json()["message"]

    container.discovery_service.limits = DiscoveryLimits()
    other_category = client.post("/api/categories", json={"name": "社区/热度", "slug": "community"})
    assert other_category.status_code == 201, other_category.text
    mismatch = client.post(
        "/api/discoveries",
        json={
            "window_start": start,
            "window_end": end,
            "category_id": other_category.json()["id"],
            "source_id": source_id,
        },
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == "discovery_scope_mismatch"

    invalid = client.post("/api/discoveries", json={"window_start": end, "window_end": start})
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "discovery_window_invalid"
    partial = client.post("/api/discoveries", json={"window_start": start})
    assert partial.status_code == 400
    assert partial.json()["code"] == "discovery_window_invalid"


@respx.mock
def test_invalid_model_output_fails_batch_with_reason(client: TestClient) -> None:
    """模型输出不符合 schema 时整批失败并保留可诊断原因，不落任何候选。"""

    configure_model(client)
    _seed(client)
    _stub(client, ScriptedProvider(raw='{"candidates": "不是数组"}'))
    start, end = _window(client)
    batch = _create(client, window_start=start, window_end=end)
    container = client.app.state.container
    # 三次尝试都用同样的假输出：任务失败并记录分类。
    for _ in range(4):
        worker = Worker(container)
        if not worker.run_once():
            break
    stored = client.get(f"/api/discoveries/{batch['id']}").json()["batch"]
    assert stored["status"] == "failed"
    assert stored["error_code"] == "llm_invalid_output"
    assert stored["candidate_count"] == 0
    assert client.get(f"/api/discoveries/{batch['id']}").json()["candidates"] == []


@respx.mock
def test_not_ready_and_missing_candidate_are_rejected(client: TestClient) -> None:
    """批次未完成或候选不存在时，选题动作给出明确的 400/404，而不是静默成功。"""

    configure_model(client)
    _seed(client)
    _stub(client, ScriptedProvider())
    start, end = _window(client)
    batch = _create(client, window_start=start, window_end=end)

    not_ready = client.post(f"/api/discoveries/{batch['id']}/candidates/whatever/select")
    assert not_ready.status_code == 400
    assert not_ready.json()["code"] == "discovery_not_ready"

    _run(client)
    missing = client.post(f"/api/discoveries/{batch['id']}/candidates/whatever/select")
    assert missing.status_code == 404
    assert missing.json()["code"] == "discovery_candidate_not_found"
    assert client.get("/api/discoveries/nope").status_code == 404
    assert client.delete("/api/discoveries/nope").status_code == 404
