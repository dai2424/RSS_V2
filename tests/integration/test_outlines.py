"""提纲集成测试：引用过滤、无效输出、版本与复用、人工保存与确认。

用假模型给出固定提纲，验证服务端的确定性规则：越界事实/引文与缺引文事实逐条丢弃
并计数、不足两节判为无效输出（不落版本行）、同一资料包重复生成复用任务、确认只对
最新版本生效且旧的已确认版本转为 superseded、确认后编辑另存新版本。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from keyword_support import configure_model
from test_digest import (
    DigestProvider,
    _digest,
    _materials_by_url,
    _prepare_materials,
    _seed_project,
)

from rss_v2.domain import DomainError, OutlineDraft, ProjectStatus, Provider, TaskStatus
from rss_v2.domain.values import new_id, now
from rss_v2.tasks.worker import Worker

#: 能在成员资料的摘要里逐字定位的引文（与 test_digest 用的是同一句）。
QUOTE_OK = "懂车帝实测中三台车支架断裂"
#: 资料里没有的引文：这条事实会被判为缺引文（missing）。
QUOTE_ABSENT = "这段话在资料正文里根本不存在，属于模型编造"


class OutlineProvider:
    """假模型：返回预设的提纲结构，用于验证服务端的过滤与状态规则。"""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls = 0
        self.user_prompt = ""

    def structured(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: Any,
        prompt_version: str,
        task_kind: str,
        schema: type[Any],
        max_output_tokens: int | None = None,
    ) -> tuple[Any, dict[str, int], int]:
        self.calls += 1
        assert schema is OutlineDraft
        self.user_prompt = prompt.user
        return OutlineDraft.model_validate(self.payload), {"total_tokens": 11}, 4


def _digest_two_facts(client: TestClient, project_id: str) -> dict[str, Any]:
    """跑一次资料整理，得到一条有引文的事实与一条缺引文的事实。"""

    materials = _materials_by_url(client, project_id)
    provider = DigestProvider(
        {
            "facts": [
                {
                    "statement": "实测中刹车踏板支架断裂，断裂瞬间受力约 1612 牛",
                    "kind": "fact",
                    "supports": [
                        {"material_id": materials["https://example.test/m1"], "quote": QUOTE_OK}
                    ],
                },
                {
                    "statement": "厂商称按高于国家标准的要求完成验证",
                    "kind": "fact",
                    "supports": [
                        {"material_id": materials["https://example.test/m1"], "quote": QUOTE_ABSENT}
                    ],
                },
            ],
            "conflicts": [],
            "gaps": [{"text": "缺少监管部门的一手通报", "blocking": True}],
        }
    )
    body = _digest(client, project_id, provider)
    assert [item["status"] for item in body["facts"]] == ["supported", "missing"]
    return body


def _generate(
    client: TestClient, project_id: str, provider: OutlineProvider, *, force: bool = False
) -> tuple[Any, dict[str, Any]]:
    """发起一次提纲生成并跑一次 worker；返回（任务响应, 列表响应）。"""

    service = client.app.state.container.outline_service
    service.runner.provider = provider
    params = {"force": "true"} if force else None
    response = client.post(f"/api/projects/{project_id}/outlines", params=params)
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.container).run_once() is True
    return response, client.get(f"/api/projects/{project_id}/outlines").json()


@respx.mock
def test_outline_filters_out_of_range_and_unsupported_references(client: TestClient) -> None:
    """越界事实/引文与缺引文事实逐条丢弃并计数；丢弃的部分看得见。"""

    configure_model(client)
    project_id = _seed_project(client, "提纲引用验收")
    _prepare_materials(client, project_id)
    digest = _digest_two_facts(client, project_id)
    supported, missing = digest["facts"][0], digest["facts"][1]
    evidence_id = supported["evidence"][0]["id"]

    provider = OutlineProvider(
        {
            "title": "实测争议：支架断裂事件",
            "angle": "从第三方实测数据出发，讲清争议的双方说法",
            "sections": [
                {
                    "heading": "发生了什么",
                    "talking_points": ["第三方实测中出现支架断裂"],
                    "fact_ids": [supported["id"], missing["id"], "不存在的事实"],
                    "evidence_ids": [evidence_id, "不存在的引文"],
                    "impact_notes": ["车主对安全性的关注度上升"],
                },
                {"heading": "", "talking_points": ["没有小标题的一节"]},
                {"heading": "行业影响", "talking_points": ["可能推动检测标准更新"]},
                {"heading": "背景", "fact_ids": [supported["id"]]},
            ],
        }
    )
    _task, body = _generate(client, project_id, provider)

    # 事实 id 与引文 id 都要出现在提示词里，否则模型只能留空或编造引文 id。
    assert f"[{supported['id']}]" in provider.user_prompt
    assert f"引文 [{evidence_id}]" in provider.user_prompt

    version = body["items"][0]
    assert [section["heading"] for section in version["sections"]] == [
        "发生了什么",
        "行业影响",
        "背景",
    ]
    first = version["sections"][0]
    # 缺引文的事实不能当依据，越界 id 一律丢弃：只留下真正有引文的那条。
    assert first["fact_ids"] == [supported["id"]]
    assert first["evidence_ids"] == [evidence_id]
    assert version["counters"] == {
        "out_of_range": 2,  # 越界事实 id + 越界引文 id
        "unsupported": 1,  # 指向 missing 事实的引用
        "dropped_sections": 1,  # 没有小标题的一节
        "dropped_points": 0,
        "without_facts": 1,  # "行业影响"一节没有任何事实依据
    }
    assert version["source"] == "model" and version["status"] == "draft"
    assert version["digest_run_id"] == digest["run"]["id"]
    assert version["material_snapshot_hash"] == digest["run"]["materials_hash"]
    assert version["model"] and version["prompt_version"] == "outline-v1"
    # 阻塞缺口只标记，不拦生成。
    assert body["has_blocking"] is True and body["blocking_count"] >= 1
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["project"]["status"] == ProjectStatus.OUTLINE_READY.value
    assert project["counts"]["outlines"] == 1
    assert project["counts"]["facts"] == 2


@respx.mock
def test_outline_rejects_insufficient_sections_without_writing_version(client: TestClient) -> None:
    """不足两节判为无效输出：任务失败且不写版本行，界面不会看到空提纲。"""

    configure_model(client)
    project_id = _seed_project(client, "无效提纲验收")
    _prepare_materials(client, project_id)
    _digest_two_facts(client, project_id)

    provider = OutlineProvider(
        {"title": "只有一节", "angle": "", "sections": [{"heading": "唯一一节"}]}
    )
    response, body = _generate(client, project_id, provider)
    task = client.app.state.container.tasks.get(str(response.json()["id"]))
    assert task is not None and task.status == "failed"
    assert task.error_code == "outline_validation_failed"
    assert body["items"] == [] and body["latest_version_id"] is None
    assert body["task_status"] == "failed"
    # 项目状态不因失败推进。
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["project"]["status"] == ProjectStatus.MATERIALS_READY.value


@respx.mock
def test_outline_reuse_force_and_lease(client: TestClient) -> None:
    """同一资料包重复生成复用任务；force 新起版本；租约失效不写版本行。"""

    configure_model(client)
    project_id = _seed_project(client, "提纲版本验收")
    _prepare_materials(client, project_id)
    digest = _digest_two_facts(client, project_id)
    fact_id = digest["facts"][0]["id"]
    provider = OutlineProvider(
        {
            "title": "标题",
            "angle": "切入点",
            "sections": [
                {"heading": "第一节", "fact_ids": [fact_id]},
                {"heading": "第二节", "fact_ids": [fact_id]},
            ],
        }
    )
    task_response, first = _generate(client, project_id, provider)
    first_calls = provider.calls
    assert first["items"][0]["version_number"] == 1

    # 同一份资料包：幂等键命中，任务复用，不再调模型、不产生新版本。
    again = client.post(f"/api/projects/{project_id}/outlines")
    assert again.status_code == 202 and again.json()["status"] == "succeeded"
    assert provider.calls == first_calls
    assert len(client.get(f"/api/projects/{project_id}/outlines").json()["items"]) == 1

    # 用户主动重新生成：新版本，历史版本保留。
    _task, forced = _generate(client, project_id, provider, force=True)
    assert provider.calls == first_calls + 1
    assert [item["version_number"] for item in forced["items"]] == [2, 1]

    # 租约失效后继续执行会被拒绝写入：不留半成品版本。
    # 直接造一条排队任务而不是再发一次 force：幂等键带 now() 秒级后缀，
    # 同一秒内的第二次 force 会命中上一条（生产上的幂等行为，但不适合用来测租约）。
    container = client.app.state.container
    service = container.outline_service
    template = container.tasks.get(str(task_response.json()["id"]))
    assert template is not None
    claimed = container.tasks.claim_next(
        ["generate_outline"],
        int(
            container.tasks.create(
                replace(
                    template,
                    id=new_id(),
                    idempotency_key=f"outline-lease:{new_id()}",
                    status=TaskStatus.QUEUED,
                    attempts=0,
                    lease_until=None,
                    lease_token=None,
                    output_version_id=None,
                    error_code=None,
                    error_message=None,
                    created_at=now(),
                    updated_at=now(),
                )
            ).created_at
        ),
        300,
    )
    assert claimed is not None
    container.tasks.reclaim_expired(int(claimed.lease_until or 0) + 1)
    with pytest.raises(DomainError) as raised:
        service.run(claimed)
    assert raised.value.code == "task_lease_lost"
    assert len(client.get(f"/api/projects/{project_id}/outlines").json()["items"]) == 2


@respx.mock
def test_outline_confirm_and_edit_flow(client: TestClient) -> None:
    """确认只对最新版本生效、重复确认幂等、确认后编辑另存新版本并作废旧确认。"""

    configure_model(client)
    project_id = _seed_project(client, "提纲确认验收")
    _prepare_materials(client, project_id)
    digest = _digest_two_facts(client, project_id)
    fact_id = digest["facts"][0]["id"]
    evidence_id = digest["facts"][0]["evidence"][0]["id"]
    provider = OutlineProvider(
        {
            "title": "生成标题",
            "angle": "生成切入点",
            "sections": [
                {"heading": "第一节", "fact_ids": [fact_id]},
                {"heading": "第二节", "fact_ids": [fact_id], "evidence_ids": [evidence_id]},
            ],
        }
    )
    _task, body = _generate(client, project_id, provider)
    version_id = body["items"][0]["id"]

    confirmed = client.post(f"/api/projects/{project_id}/outlines/{version_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["version"]["status"] == "confirmed" and payload["version"]["confirmed_at"]
    assert payload["confirmed_version_id"] == version_id
    assert payload["has_unconfirmed_changes"] is False
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["project"]["status"] == ProjectStatus.OUTLINE_CONFIRMED.value

    # 重复确认幂等：状态与时间戳不变。
    again = client.post(f"/api/projects/{project_id}/outlines/{version_id}/confirm").json()
    assert again["version"]["status"] == "confirmed"
    assert again["version"]["confirmed_at"] == payload["version"]["confirmed_at"]

    # 确认后编辑：另存新版本（人工稿），旧版本不被改写、仍是 confirmed。
    saved = client.put(
        f"/api/projects/{project_id}/outlines/{version_id}",
        json={
            "title": "人工改过的标题",
            "angle": "人工切入点",
            "sections": [
                {"heading": "改过的第一节", "talking_points": ["要点"], "fact_ids": [fact_id]},
                {"heading": "第二节", "fact_ids": [fact_id], "evidence_ids": [evidence_id]},
            ],
        },
    )
    assert saved.status_code == 201, saved.text
    new_version = saved.json()["version"]
    assert new_version["version_number"] == 2
    assert new_version["source"] == "human" and new_version["status"] == "draft"
    assert new_version["based_on_version"] == version_id
    assert new_version["prompt_version"] is None and new_version["model"] is None
    assert new_version["digest_run_id"] == digest["run"]["id"]
    assert saved.json()["has_unconfirmed_changes"] is True
    assert client.get(f"/api/projects/{project_id}/outlines").json()["items"][1]["status"] == (
        "confirmed"
    )

    # 非最新版本不能确认。
    stale = client.post(f"/api/projects/{project_id}/outlines/{version_id}/confirm")
    assert stale.status_code == 409 and stale.json()["code"] == "outline_version_stale"

    # 确认新版本：旧的已确认版本转为 superseded。
    second = client.post(f"/api/projects/{project_id}/outlines/{new_version['id']}/confirm").json()
    assert second["confirmed_version_id"] == new_version["id"]
    items = client.get(f"/api/projects/{project_id}/outlines").json()["items"]
    statuses = {item["id"]: item["status"] for item in items}
    assert statuses[version_id] == "superseded"
    assert statuses[new_version["id"]] == "confirmed"

    # 已取代的版本不能再确认；但可以作为人工稿的基准（从旧版改回来是正常需求）。
    blocked = client.post(f"/api/projects/{project_id}/outlines/{version_id}/confirm")
    assert blocked.status_code == 409 and blocked.json()["code"] == "outline_version_superseded"
    restored = client.put(
        f"/api/projects/{project_id}/outlines/{version_id}",
        json={
            "title": "从旧版改回来",
            "angle": "",
            "sections": [{"heading": "一"}, {"heading": "二"}],
        },
    )
    assert restored.status_code == 201
    assert restored.json()["version"]["version_number"] == 3
    assert restored.json()["version"]["based_on_version"] == version_id


@respx.mock
def test_outline_requires_digest_and_validates_human_save(client: TestClient) -> None:
    """没有资料整理结果时明确拒绝；人工稿同样要过引用校验。"""

    configure_model(client)
    project_id = _seed_project(client, "提纲前置验收")
    empty = client.post(f"/api/projects/{project_id}/outlines")
    assert empty.status_code == 400
    assert empty.json()["code"] == "project_status_invalid"

    _prepare_materials(client, project_id)
    digest = _digest_two_facts(client, project_id)
    fact_id = digest["facts"][0]["id"]
    provider = OutlineProvider(
        {
            "title": "标题",
            "angle": "",
            "sections": [{"heading": "一", "fact_ids": [fact_id]}, {"heading": "二"}],
        }
    )
    _task, body = _generate(client, project_id, provider)
    version_id = body["items"][0]["id"]

    # 只有一节：与模型输出同一口径判为无效。
    too_short = client.put(
        f"/api/projects/{project_id}/outlines/{version_id}",
        json={"title": "改", "angle": "", "sections": [{"heading": "只有一节"}]},
    )
    assert too_short.status_code == 400
    assert too_short.json()["code"] == "outline_validation_failed"

    # 越界事实与引文被丢弃：不报错，但新版本里不会留下它们。
    filtered = client.put(
        f"/api/projects/{project_id}/outlines/{version_id}",
        json={
            "title": "改",
            "angle": "",
            "sections": [
                {"heading": "一", "fact_ids": ["不存在的事实"]},
                {"heading": "二", "fact_ids": [fact_id], "evidence_ids": ["不存在的引文"]},
            ],
        },
    )
    assert filtered.status_code == 201
    sections = filtered.json()["version"]["sections"]
    assert sections[0]["fact_ids"] == [] and sections[1]["evidence_ids"] == []
    assert filtered.json()["version"]["counters"] == {
        "out_of_range": 2,
        "unsupported": 0,
        "dropped_sections": 0,
        "dropped_points": 0,
        "without_facts": 1,
    }

    # 不存在的版本按不存在处理。
    missing = client.put(
        f"/api/projects/{project_id}/outlines/不存在",
        json={"title": "改", "angle": "", "sections": [{"heading": "一"}, {"heading": "二"}]},
    )
    assert missing.status_code == 404 and missing.json()["code"] == "outline_not_found"
