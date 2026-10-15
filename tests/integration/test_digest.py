"""资料整理集成测试：引文定位、事实/分歧/缺口落库、版本与重跑、租约。

用假模型给出固定事实清单，验证服务端的确定性规则：引文必须在资料正文里能定位
（改写过的引文一律丢弃并计数）、没有引文的事实记为缺料并自动成为阻塞缺口、
重跑产生新版本而旧依据保留、已处理的缺口在重跑后保持已处理。
"""

from __future__ import annotations

from typing import Any

import respx
from fastapi.testclient import TestClient
from keyword_support import collect, configure_model, create_source, disable_enrich
from test_materials import FakeReader, FakeSearch, QueryProvider, _page, _two_item_feed

from rss_v2.domain import (
    BUDGET_EXHAUSTED,
    DigestLimits,
    DomainError,
    MaterialDigest,
    MaterialStatus,
    ProjectStatus,
    Provider,
    SearchHit,
    WritingProject,
)
from rss_v2.domain.values import new_id, now
from rss_v2.tasks.worker import Worker

#: 两条成员消息的正文（资料准备的夹具里，成员正文来自回读的页面）。
BODY_ONE = "懂车帝实测中三台尊界 V800 的刹车踏板支架全部断裂，断裂瞬间受力约 1612 牛。" * 3
BODY_TWO = "尊界回应称制动系统按照高于国家标准的要求完成验证，并将为车主免费升级。" * 3


class DigestProvider:
    """假模型：返回预设的资料整理结果；可指定"引文被改写"等异常形态。"""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls = 0

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
        assert schema is MaterialDigest
        return MaterialDigest.model_validate(self.payload), {"total_tokens": 7}, 3


def _seed_project(client: TestClient, title: str) -> str:
    """建两条消息 → 人工线索 → 写作项目；返回 project_id。"""

    url = f"https://digest.test/{title[:6]}.xml"
    respx.get(url).mock(return_value=__import__("httpx").Response(200, content=_two_item_feed()))
    source_id = create_source(client, url, name=f"整理验收来源{title[:4]}")
    disable_enrich(client, source_id)
    collect(client, source_id)
    messages = client.get("/api/messages", params={"limit": "10", "source_id": source_id}).json()[
        "items"
    ]
    lead = client.post(
        "/api/leads",
        json={
            "title": title,
            "message_ids": [str(item["id"]) for item in messages],
            "note": "同一事件的两篇报道",
        },
    ).json()
    container = client.app.state.container
    timestamp = now()
    project = container.writing_projects.create(
        WritingProject(
            id=new_id(),
            lead_id=str(lead["id"]),
            candidate_id=None,
            title=title,
            status=ProjectStatus.MATERIALS_PENDING.value,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    return project.id


def _prepare_materials(client: TestClient, project_id: str) -> dict[str, Any]:
    """跑一次资料准备：两条成员链接回读成正文，检索命中的网页也落库。"""

    pages = {
        "https://example.test/m1": _page("https://example.test/m1", BODY_ONE),
        "https://example.test/m2": _page("https://example.test/m2", BODY_TWO),
        "https://news.test/report": _page("https://news.test/report", "第三方测试报道" * 60),
    }
    search = FakeSearch(
        {
            "中文检索式": [
                SearchHit(
                    title="第三方测试报道",
                    url="https://news.test/report",
                    snippet="",
                    engine="bing",
                )
            ]
        }
    )
    service = client.app.state.container.material_service
    service.runner.search = search
    service.runner.reader = FakeReader(pages)
    service.runner.provider = QueryProvider([("中文检索式", "中文报道")])
    response = client.post(f"/api/projects/{project_id}/materials")
    assert response.status_code == 202
    assert Worker(client.app.state.container).run_once() is True
    return client.get(f"/api/projects/{project_id}/materials").json()


def _materials_by_url(client: TestClient, project_id: str) -> dict[str, str]:
    """URL → 材料 ID：构造引用时要用真实 ID。"""

    body = client.get(f"/api/projects/{project_id}/materials").json()
    return {item["url"]: item["id"] for item in body["items"]}


def _digest(
    client: TestClient, project_id: str, provider: DigestProvider, limits=None
) -> dict[str, Any]:
    service = client.app.state.container.digest_service
    service.provider = provider
    if limits is not None:
        service.limits = limits
    response = client.post(f"/api/projects/{project_id}/facts")
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.container).run_once() is True
    return client.get(f"/api/projects/{project_id}/facts").json()


@respx.mock
def test_digest_links_facts_to_verifiable_quotes(client: TestClient) -> None:
    """事实带引文与来源资料；冲突与缺口落成待办；计数如实。"""

    configure_model(client)
    project_id = _seed_project(client, "尊界 V800 争议")
    _prepare_materials(client, project_id)
    materials = _materials_by_url(client, project_id)
    first = materials["https://example.test/m1"]
    second = materials["https://example.test/m2"]
    provider = DigestProvider(
        {
            "facts": [
                {
                    "statement": "懂车帝实测三台车刹车踏板支架断裂，断裂受力约 1612 牛",
                    "kind": "fact",
                    "supports": [
                        {"material_id": first, "quote": "三台尊界 V800 的刹车踏板支架全部断裂"},
                        {"material_id": "不存在的材料", "quote": "这条引用越界"},
                    ],
                },
                {
                    "statement": "厂商称按高于国家标准的要求完成验证并免费升级",
                    "kind": "fact",
                    "supports": [
                        {"material_id": second, "quote": "制动系统按照高于国家标准的要求完成验证"}
                    ],
                },
                {
                    "statement": "这起事件可能会影响该车型的销量",
                    "kind": "推测",
                    "supports": [
                        {"material_id": second, "quote": "这句话被模型改写过，资料里没有"}
                    ],
                },
            ],
            "conflicts": [{"text": "厂商声明与实际测试结果存在分歧", "statement_indexes": [0, 1]}],
            "gaps": [
                {"text": "缺少监管部门的一手召回或缺陷调查通报", "blocking": True},
                {"text": "缺少第三方独立检测机构结论", "blocking": False},
            ],
        }
    )

    body = _digest(client, project_id, provider)
    counts = body["run"]["counts"]
    assert counts["fact"] == 2 and counts["speculation"] == 1
    assert counts["evidence"] == 2  # 两条可定位的引文；越界与被改写的各丢一条
    assert counts["out_of_range"] == 1
    assert counts["rejected_quotes"] == 1
    assert counts["conflict"] == 1 and counts["gap"] >= 3  # 两个模型缺口 + 缺料事实自动补一条

    facts = body["facts"]
    assert facts[0]["status"] == "conflicting"  # 分歧涉及第 0 与第 1 条
    assert facts[1]["status"] == "conflicting"
    assert "分歧" in facts[0]["conflict_note"]
    assert facts[2]["status"] == "missing" and facts[2]["evidence"] == []
    assert facts[0]["evidence"][0]["material_id"] == first
    assert facts[0]["evidence"][0]["locator"] >= 0  # 引文在正文里定位到了

    gaps = [note for note in body["notes"] if note["kind"] == "gap"]
    assert any(note["blocking"] for note in gaps)
    assert any("缺少可用引文" in note["text"] for note in gaps)
    assert body["has_blocking"] is True

    # 项目摘要：阻塞数落库，后续阶段据此判断能否进提纲。
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["project"]["status"] == ProjectStatus.MATERIALS_READY.value


@respx.mock
def test_digest_versions_and_note_resolution_survive_rerun(client: TestClient) -> None:
    """重跑产生新版本、旧依据保留；已处理的缺口在重跑后保持已处理。"""

    configure_model(client)
    project_id = _seed_project(client, "版本验收选题")
    _prepare_materials(client, project_id)
    materials = _materials_by_url(client, project_id)
    provider = DigestProvider(
        {
            "facts": [
                {
                    "statement": "事实一",
                    "kind": "fact",
                    "supports": [
                        {
                            "material_id": materials["https://example.test/m1"],
                            "quote": "三台尊界 V800 的刹车踏板支架全部断裂",
                        }
                    ],
                }
            ],
            "conflicts": [],
            "gaps": [{"text": "缺少官方一手资料", "blocking": True}],
        }
    )

    first = _digest(client, project_id, provider)
    first_run = first["run"]["id"]
    note = next(item for item in first["notes"] if item["blocking"])
    resolved = client.post(f"/api/projects/{project_id}/notes/{note['id']}/resolve")
    assert resolved.status_code == 200 and resolved.json()["note"]["resolved_at"]

    # 重跑（同一份资料包）：幂等键命中，任务复用，不产生新版本。
    again = client.post(f"/api/projects/{project_id}/facts")
    assert again.status_code == 202
    assert again.json()["status"] == "succeeded"
    assert provider.calls == 1

    # 换一份资料（新增一篇网页）后重跑：新版本，旧版本仍可查看，已处理的缺口保留。
    service = client.app.state.container.material_service
    service.runner.search = FakeSearch(
        {
            "中文检索式": [
                SearchHit(title="新报道", url="https://news.test/new", snippet="", engine="bing")
            ]
        }
    )
    service.runner.reader = FakeReader(
        {"https://news.test/new": _page("https://news.test/new", "新资料正文" * 80)}
    )
    service.runner.provider = QueryProvider([("中文检索式", "补充资料")])
    assert (
        client.post(f"/api/projects/{project_id}/materials", params={"force": "true"}).status_code
        == 202
    )
    assert Worker(client.app.state.container).run_once() is True

    second = _digest(client, project_id, provider)
    assert second["run"]["id"] != first_run
    assert provider.calls == 2
    history = client.get(f"/api/projects/{project_id}/digest-runs").json()["items"]
    assert [item["id"] for item in history][:2] == [second["run"]["id"], first_run]
    # 旧版本的缺口与事实仍在（按版本查询）。
    old = client.get(f"/api/projects/{project_id}/facts", params={"run_id": first_run}).json()
    assert old["run"]["id"] == first_run and old["facts"]
    assert any(item["resolved_at"] for item in old["notes"])


@respx.mock
def test_digest_requires_materials_and_lease(client: TestClient) -> None:
    """没有可用资料时明确拒绝；租约失效后继续执行会被拒绝写入。"""

    configure_model(client)
    project_id = _seed_project(client, "缺料验收选题")
    empty = client.post(f"/api/projects/{project_id}/facts")
    assert empty.status_code == 400
    assert empty.json()["code"] == "digest_no_materials"

    _prepare_materials(client, project_id)
    materials = _materials_by_url(client, project_id)
    provider = DigestProvider(
        {
            "facts": [
                {
                    "statement": "事实一",
                    "kind": "fact",
                    "supports": [
                        {
                            "material_id": materials["https://example.test/m1"],
                            "quote": "三台尊界 V800 的刹车踏板支架全部断裂",
                        }
                    ],
                }
            ],
            "conflicts": [],
            "gaps": [],
        }
    )
    service = client.app.state.container.digest_service
    service.provider = provider
    service.limits = DigestLimits()
    response = client.post(f"/api/projects/{project_id}/facts")
    task = client.app.state.container.tasks.get(str(response.json()["id"]))
    claimed = client.app.state.container.tasks.claim_next(
        ["digest_materials"], int(task.created_at), 300
    )
    assert claimed is not None
    client.app.state.container.tasks.reclaim_expired(int(claimed.lease_until or 0) + 1)

    import pytest

    with pytest.raises(DomainError) as raised:
        service.run(claimed)
    assert raised.value.code == "task_lease_lost"
    # 失败版本留在库里，事实一条没写。
    runs = client.get(f"/api/projects/{project_id}/digest-runs").json()["items"]
    assert len(runs) == 1


@respx.mock
def test_digest_only_uses_read_materials(client: TestClient) -> None:
    """只把已读资料喂给模型：失败/未读取的资料不能成为证据来源。"""

    configure_model(client)
    project_id = _seed_project(client, "证据来源选题")
    service = client.app.state.container.material_service
    service.runner.search = FakeSearch({"中文检索式": []})
    service.runner.reader = FakeReader(
        {
            "https://example.test/m1": _page("https://example.test/m1", BODY_ONE),
            "https://example.test/m2": _page("https://example.test/m2", BODY_TWO),
        }
    )
    service.runner.provider = QueryProvider([("中文检索式", "中文报道")])
    service.limits = service.limits.__class__(max_reads=0)  # 预算为 0：成员链接也不回读
    assert client.post(f"/api/projects/{project_id}/materials").status_code == 202
    assert Worker(client.app.state.container).run_once() is True
    body = client.get(f"/api/projects/{project_id}/materials").json()
    assert body["counts"].get("read", 0) == 0  # 成员只有摘要（没有读到正文的资料）
    assert all(item["error_code"] in {BUDGET_EXHAUSTED, ""} for item in body["items"])

    materials = _materials_by_url(client, project_id)
    provider = DigestProvider(
        {
            "facts": [
                {
                    "statement": "只有摘要的资料也能提供引文",
                    "kind": "fact",
                    "supports": [
                        {
                            "material_id": materials["https://example.test/m1"],
                            "quote": "懂车帝实测中三台车支架断裂",
                        }
                    ],
                }
            ],
            "conflicts": [],
            "gaps": [],
        }
    )
    view = _digest(client, project_id, provider)
    assert view["facts"][0]["status"] == "supported"
    assert view["facts"][0]["evidence"][0]["material_id"] == materials["https://example.test/m1"]
    statuses = {
        item["url"]: item["status"]
        for item in client.get(f"/api/projects/{project_id}/materials").json()["items"]
    }
    assert statuses["https://example.test/m1"] == MaterialStatus.SUMMARY_ONLY.value
