"""全文集成测试：生成、引用校验、来源推导、版本与预览。

用假模型给出固定的 ArticleDraft，验证服务端的确定性规则：越界/悬空的引用与
无引用的事实段分别怎么处理、推测段怎么降级、来源列表由正文推导、同一提纲重复生成
复用任务、预览 HTML 不产生可执行内容。
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
from test_outlines import OutlineProvider

from rss_v2.domain import (
    ArticleDraft,
    DomainError,
    ExternalServiceError,
    ProjectStatus,
    Provider,
    TaskStatus,
)
from rss_v2.domain.values import new_id, now
from rss_v2.tasks.worker import Worker

#: 能在成员资料的摘要里逐字定位的引文（与 test_digest 用的是同一句）。
QUOTE_OK = "懂车帝实测中三台车支架断裂"
#: 资料里没有的引文：这条事实会被判为缺引文（missing）。
QUOTE_ABSENT = "这段话在资料正文里根本不存在，属于模型编造"


class ArticleProvider:
    """假模型：返回预设的全文结构，用于验证服务端的校验与降级规则。"""

    def __init__(self, payload: dict[str, Any] | None = None, *, error: Exception | None = None):
        self.payload = payload
        self.error = error
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
        assert schema is ArticleDraft
        self.user_prompt = prompt.user
        if self.error is not None:
            raise self.error
        return ArticleDraft.model_validate(self.payload), {"total_tokens": 21}, 9


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
                        {
                            "material_id": materials["https://example.test/m1"],
                            "quote": QUOTE_OK,
                        }
                    ],
                },
                {
                    "statement": "厂商称按高于国家标准的要求完成验证",
                    "kind": "fact",
                    "supports": [
                        {
                            "material_id": materials["https://example.test/m1"],
                            "quote": QUOTE_ABSENT,
                        }
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


def _confirm_outline(client: TestClient, project_id: str, fact_id: str) -> str:
    """跑一次提纲生成并确认：全文生成的前置。"""

    container = client.app.state.container
    provider = OutlineProvider(
        {
            "title": "生成标题",
            "angle": "生成切入点",
            "sections": [
                {"heading": "第一节", "fact_ids": [fact_id]},
                {"heading": "第二节"},
            ],
        }
    )
    container.outline_service.runner.provider = provider
    response = client.post(f"/api/projects/{project_id}/outlines")
    assert response.status_code == 202, response.text
    assert Worker(container).run_once() is True
    items = client.get(f"/api/projects/{project_id}/outlines").json()["items"]
    version_id = items[0]["id"]
    confirmed = client.post(f"/api/projects/{project_id}/outlines/{version_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return str(version_id)


def _project_with_outline(client: TestClient) -> tuple[str, dict[str, Any]]:
    """建项目 → 资料准备 → 资料整理 → 提纲确认；返回（项目 id, 资料整理响应）。"""

    configure_model(client)
    project_id = _seed_project(client, "全文验收")
    _prepare_materials(client, project_id)
    digest = _digest_two_facts(client, project_id)
    _confirm_outline(client, project_id, digest["facts"][0]["id"])
    return project_id, digest


def _generate(
    client: TestClient, project_id: str, provider: ArticleProvider, *, force: bool = False
) -> tuple[Any, dict[str, Any]]:
    """发起一次全文生成并跑一次 worker；返回（任务响应, 列表响应）。"""

    service = client.app.state.container.article_service
    service.runner.provider = provider
    params = {"force": "true"} if force else None
    response = client.post(f"/api/projects/{project_id}/articles", params=params)
    assert response.status_code == 202, response.text
    assert Worker(client.app.state.container).run_once() is True
    return response, client.get(f"/api/projects/{project_id}/articles").json()


def _payload(
    fact_id: str,
    evidence_id: str,
    *,
    extra_citations: list[dict[str, Any]] | None = None,
    source_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一份结构完整的全文输出：两节、事实段带标记、推测段带不确定表述。"""

    return {
        "title": "实测争议：支架断裂事件",
        "lead": "第三方实测与厂商说法不一，本文梳理已知事实与待核实项。",
        "sections": [
            {
                "heading": "发生了什么",
                "paragraphs": [
                    {"text": "第三方实测中出现支架断裂[^1]。", "kind": "fact"},
                    {"text": "这可能推动检测标准更新[^2]。", "kind": "speculation"},
                    {"text": "从行业角度看，这起事件值得关注。", "kind": "analysis"},
                ],
            },
            {
                "heading": "行业影响",
                "paragraphs": [{"text": "同类产品可能被要求补充验证[^1]。", "kind": "analysis"}],
            },
        ],
        "citations": [
            {"marker": "1", "fact_id": fact_id, "evidence_ids": [evidence_id]},
            # 不给引文 id：服务端按事实自己的引文补全，模型漏填不等于没有依据。
            {"marker": "2", "fact_id": fact_id, "evidence_ids": []},
            *(extra_citations or []),
        ],
        "source_list": source_list or [],
    }


@respx.mock
def test_article_generates_with_sources_and_preview(client: TestClient) -> None:
    """正常生成：版本落库、项目状态推进、来源由正文推导、预览带证据且可点。"""

    project_id, digest = _project_with_outline(client)
    fact = digest["facts"][0]
    provider = ArticleProvider(_payload(fact["id"], fact["evidence"][0]["id"]))
    _task, body = _generate(client, project_id, provider)

    # 已确认提纲的分节与事实都真的进了提示词。
    assert "第一节" in provider.user_prompt
    assert fact["id"] in provider.user_prompt

    version = body["items"][0]
    assert version["kind"] == "generated" and version["status"] == "draft"
    assert version["version_number"] == 1 and version["is_latest"] is True
    assert version["source_count"] == 1
    assert version["counters"] == {
        "out_of_range": 0,
        "unsupported": 0,
        "unused_citations": 0,
        "duplicate_markers": 0,
        "invalid_markers": 0,
        "downgraded_speculation": 0,
        "unknown_kind": 0,
        "rejected_links": 0,
        "dropped_paragraphs": 0,
        "dropped_sections": 0,
        "invalid_sources": 0,
        "unused_sources": 0,
        "added_sources": 0,
        "dropped_sources": 0,
    }
    assert body["has_blocking"] is True and body["blocking_count"] >= 1
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["project"]["status"] == ProjectStatus.ARTICLE_READY.value
    assert project["counts"]["articles"] == 1

    # 单个版本：结构化正文 + 由它派生的 Markdown。
    detail = client.get(f"/api/projects/{project_id}/articles/{version['id']}").json()
    assert detail["title"] == "实测争议：支架断裂事件"
    assert [section["heading"] for section in detail["sections"]] == ["发生了什么", "行业影响"]
    assert detail["sections"][0]["paragraphs"][1]["kind"] == "speculation"
    assert detail["body_markdown"].startswith("# 实测争议：支架断裂事件")
    assert "## 发生了什么" in detail["body_markdown"]
    assert "[^1]" in detail["body_markdown"]

    # 预览：受控 HTML + 引用证据 + 待核对项；标记渲染成按钮而不是链接。
    preview = client.get(f"/api/projects/{project_id}/articles/{version['id']}/preview").json()
    assert "<h1>" in preview["html"] and "<h2>发生了什么</h2>" in preview["html"]
    assert 'data-marker="1"' in preview["html"] and 'data-marker="2"' in preview["html"]
    assert "<script" not in preview["html"]
    assert "source-list" in preview["html"]
    assert preview["single_source"] is True
    assert preview["has_blocking"] is True
    citation = next(item for item in preview["citations"] if item["marker"] == "1")
    assert citation["fact_id"] == fact["id"]
    assert citation["fact_status"] == "supported"
    assert citation["statement"].startswith("实测中刹车踏板支架断裂")
    assert citation["quotes"][0]["text"] == QUOTE_OK
    assert citation["quotes"][0]["material_url"].startswith("https://")
    assert any(note["blocking"] for note in preview["pending_notes"])


@respx.mock
def test_article_drops_unused_out_of_range_citation(client: TestClient) -> None:
    """未被正文使用的引用（含越界事实）丢弃并计数，生成继续。"""

    project_id, digest = _project_with_outline(client)
    fact = digest["facts"][0]
    payload = _payload(
        fact["id"],
        fact["evidence"][0]["id"],
        extra_citations=[
            {"marker": "7", "fact_id": "不存在的事实", "evidence_ids": []},
            {"marker": "8", "fact_id": digest["facts"][1]["id"], "evidence_ids": []},
        ],
        source_list=[
            {"material_id": "不存在的资料", "title": "编造", "publisher": "", "url": "https://x"}
        ],
    )
    _task, body = _generate(client, project_id, ArticleProvider(payload))
    counters = body["items"][0]["counters"]
    # 越界事实 1 条；指向 missing 事实的 1 条；两条都没被正文用到，因此只是丢弃。
    assert counters["out_of_range"] == 1
    assert counters["unsupported"] == 1
    assert counters["invalid_sources"] == 1
    assert len(body["items"]) == 1


@respx.mock
def test_article_rejects_dangling_marker_and_uncited_fact(client: TestClient) -> None:
    """悬空标记与没有引用的事实段都判整次失败，不写版本行。"""

    project_id, digest = _project_with_outline(client)
    fact = digest["facts"][0]
    evidence_id = fact["evidence"][0]["id"]

    dangling = _payload(fact["id"], evidence_id)
    dangling["sections"][0]["paragraphs"][0]["text"] = "第三方实测中出现支架断裂[^9]。"
    _task, body = _generate(client, project_id, ArticleProvider(dangling))
    assert body["items"] == []
    task_id = client.get("/api/tasks?task_type=generate_article").json()["items"][0]["id"]
    failed = client.app.state.container.tasks.get(str(task_id))
    assert failed is not None and failed.status == "failed"
    assert failed.error_code == "article_validation_failed"

    uncited = _payload(fact["id"], evidence_id)
    uncited["sections"][0]["paragraphs"][1] = {
        "text": "这一节没有任何引用。",
        "kind": "fact",
    }
    _task, body = _generate(client, project_id, ArticleProvider(uncited), force=True)
    assert body["items"] == []
    assert (
        client.get(f"/api/projects/{project_id}").json()["project"]["status"]
        == ProjectStatus.OUTLINE_CONFIRMED.value
    )


@respx.mock
def test_article_downgrades_speculation_and_derives_sources(client: TestClient) -> None:
    """推测段缺不确定表述时降级为分析；来源按正文引用推导并计数漏列/多列。"""

    project_id, digest = _project_with_outline(client)
    materials = _materials_by_url(client, project_id)
    fact = digest["facts"][0]
    payload = _payload(
        fact["id"],
        fact["evidence"][0]["id"],
        # 模型列了一份**没被正文引用**的资料，却漏了正文真正引用的那份。
        source_list=[{"material_id": materials["https://example.test/m2"]}],
    )
    payload["sections"][0]["paragraphs"][1] = {
        "text": "检测标准会被推动更新[^2]。",  # 没有不确定表述
        "kind": "speculation",
    }
    payload["sections"][0]["paragraphs"].append({"text": "", "kind": "fact"})
    _task, body = _generate(client, project_id, ArticleProvider(payload))

    version = body["items"][0]
    assert version["counters"]["downgraded_speculation"] == 1
    assert version["counters"]["dropped_paragraphs"] == 1
    assert version["counters"]["unused_sources"] == 1  # 列了 m2，正文没引用
    assert version["counters"]["added_sources"] == 1  # 漏了正文引用的 m1，服务端补上
    detail = client.get(f"/api/projects/{project_id}/articles/{version['id']}").json()
    assert detail["sections"][0]["paragraphs"][1]["kind"] == "analysis"
    # 来源列表以正文推导为准：只有 m1，且字段来自资料行。
    assert [item["material_id"] for item in detail["source_list"]] == [
        materials["https://example.test/m1"]
    ]
    assert detail["source_list"][0]["title"]


@respx.mock
def test_article_requires_confirmed_outline(client: TestClient) -> None:
    """没有已确认的提纲时明确拒绝（400）；确认后但提纲被改过仍按已确认版本生成。"""

    configure_model(client)
    project_id = _seed_project(client, "全文前置验收")
    _prepare_materials(client, project_id)
    digest = _digest_two_facts(client, project_id)
    empty = client.post(f"/api/projects/{project_id}/articles")
    assert empty.status_code == 400
    assert empty.json()["code"] == "outline_not_confirmed"

    outline_id = _confirm_outline(client, project_id, digest["facts"][0]["id"])
    # 确认后再另存一版人工提纲：最新版本不是已确认版本，但全文仍按确认版本生成。
    saved = client.put(
        f"/api/projects/{project_id}/outlines/{outline_id}",
        json={
            "title": "改过的标题",
            "angle": "",
            "sections": [{"heading": "一"}, {"heading": "二"}],
        },
    )
    assert saved.status_code == 201
    provider = ArticleProvider(
        _payload(digest["facts"][0]["id"], digest["facts"][0]["evidence"][0]["id"])
    )
    _task, body = _generate(client, project_id, provider)
    assert body["has_unconfirmed_changes"] is True
    assert body["outline_version_id"] == outline_id
    assert body["items"][0]["outline_version_id"] == outline_id


@respx.mock
def test_article_reuse_force_and_lease(client: TestClient) -> None:
    """同一提纲重复生成复用任务；force 新起版本；租约失效不写版本行。"""

    project_id, digest = _project_with_outline(client)
    fact = digest["facts"][0]
    provider = ArticleProvider(_payload(fact["id"], fact["evidence"][0]["id"]))
    response, first = _generate(client, project_id, provider)
    calls = provider.calls
    assert first["items"][0]["version_number"] == 1

    again = client.post(f"/api/projects/{project_id}/articles")
    assert again.status_code == 202 and again.json()["status"] == "succeeded"
    assert provider.calls == calls

    _task, forced = _generate(client, project_id, provider, force=True)
    assert provider.calls == calls + 1
    assert [item["version_number"] for item in forced["items"]] == [2, 1]

    container = client.app.state.container
    template = container.tasks.get(str(response.json()["id"]))
    assert template is not None
    claimed = container.tasks.claim_next(
        ["generate_article"],
        int(
            container.tasks.create(
                replace(
                    template,
                    id=new_id(),
                    idempotency_key=f"article-lease:{new_id()}",
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
        container.article_service.run(claimed)
    assert raised.value.code == "task_lease_lost"
    assert len(client.get(f"/api/projects/{project_id}/articles").json()["items"]) == 2


@respx.mock
def test_article_truncation_writes_no_version(client: TestClient) -> None:
    """输出被截断（外部服务错误）不写版本行，项目状态不推进。"""

    project_id, _digest_body = _project_with_outline(client)
    provider = ArticleProvider(error=ExternalServiceError("llm_output_truncated", "被截断"))
    _task, body = _generate(client, project_id, provider)
    assert body["items"] == []
    assert client.get(f"/api/projects/{project_id}").json()["project"]["status"] == (
        ProjectStatus.OUTLINE_CONFIRMED.value
    )


@respx.mock
def test_article_preview_escapes_untrusted_text(client: TestClient) -> None:
    """正文里的 HTML 与非法链接按不可信内容处理：预览只产出转义后的文字。"""

    project_id, digest = _project_with_outline(client)
    fact = digest["facts"][0]
    payload = _payload(fact["id"], fact["evidence"][0]["id"])
    payload["sections"][0]["paragraphs"][2] = {
        "text": "<script>alert(1)</script>[x](javascript:alert(1)) 安全链接[官网](https://example.test/a)。",
        "kind": "analysis",
    }
    _task, body = _generate(client, project_id, ArticleProvider(payload))
    version = body["items"][0]
    preview = client.get(f"/api/projects/{project_id}/articles/{version['id']}/preview").json()
    assert "<script" not in preview["html"]
    assert "javascript:" not in preview["html"]
    assert "&lt;script&gt;" in preview["html"]
    assert 'href="https://example.test/a"' in preview["html"]
    # 非法链接在生成阶段就被还原成纯文字并计数。
    assert version["counters"]["rejected_links"] == 1


@respx.mock
def test_article_not_found(client: TestClient) -> None:
    """不存在的版本按不存在处理。"""

    project_id, _digest_body = _project_with_outline(client)
    missing = client.get(f"/api/projects/{project_id}/articles/不存在")
    assert missing.status_code == 404
    assert missing.json()["code"] == "article_not_found"


@respx.mock
def test_article_tolerates_null_fields(client: TestClient) -> None:
    """模型把可选字段写成 null 时按"没给"处理，不因一个 null 判整份失败。

    真实环境实测（deepseek-v4.1-flash）：来源候选会写成 `"material_id": null`，
    严格的 `str` 类型会让整篇生成失败——脏数据应该被丢弃并计数，而不是整份作废。
    """

    project_id, digest = _project_with_outline(client)
    fact = digest["facts"][0]
    payload = _payload(fact["id"], fact["evidence"][0]["id"])
    payload["sections"][0]["paragraphs"].append({"text": None, "kind": None})
    payload["citations"].append({"marker": None, "fact_id": None, "evidence_ids": [None]})
    payload["source_list"] = [
        {"material_id": None, "title": None, "publisher": None},
        # 数字 id 会被归一成字符串，再按"不存在"丢弃并计数。
        {"material_id": 42, "title": None},
    ]

    _task, body = _generate(client, project_id, ArticleProvider(payload))
    version = body["items"][0]
    assert version["counters"]["invalid_markers"] == 1
    assert version["counters"]["dropped_paragraphs"] == 1
    assert version["counters"]["invalid_sources"] == 1  # 空 id 当作没给；数字 id 计为自造
    assert version["source_count"] == 1
