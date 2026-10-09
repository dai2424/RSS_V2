"""提示词库、任务分配与试跑的集成测试。"""

import sqlite3

import respx
from fastapi.testclient import TestClient
from httpx import Response

from rss_v2.bootstrap import Container
from rss_v2.domain import EnrichmentResult, ExternalServiceError, Provider, TranslationResult
from rss_v2.llm import PromptPayload
from rss_v2.tasks.worker import Worker

CATEGORY_ID = "8cabd1f6-c0c3-4b32-863d-3836cf5a8171"
LONG_ENGLISH_SUMMARY = "This description is long enough to trigger enrichment " * 10
TRANSLATE_KEY = "translation"
ENRICH_KEY = "enrich"


def feed(title: str, summary: str) -> bytes:
    """构造只提供 description 的最小 RSS。"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Example Tech</title>
<link>https://example.test/</link>
<description>Example feed</description>
<item>
  <guid>entry-1</guid>
  <title>{title}</title>
  <link>https://example.test/entry-1</link>
  <description><![CDATA[{summary}]]></description>
  <pubDate>Tue, 07 Oct 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>"""


class FakeProvider:
    """记录渲染后的提示词，可按键或模型模拟失败。"""

    def __init__(self, failures: dict[str, str] | None = None) -> None:
        self.failures = failures or {}
        self.prompts: list[PromptPayload] = []

    def _check(self, prompt: PromptPayload, secret: str) -> None:
        self.prompts.append(prompt)
        if code := self.failures.get(secret):
            raise ExternalServiceError(code, "模拟失败")

    def translate(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[TranslationResult, dict[str, int], int]:
        self._check(prompt, secret)
        return TranslationResult("中文标题", "中文摘要", "中文正文"), {"total_tokens": 3}, 7

    def enrich(
        self,
        provider: Provider,
        model: str,
        secret: str,
        prompt: PromptPayload,
        prompt_version: str,
    ) -> tuple[EnrichmentResult, dict[str, int], int]:
        self._check(prompt, secret)
        return EnrichmentResult("精简标题", "摘要", ("关键词",)), {"total_tokens": 5}, 9


def create_source(client: TestClient, url: str, name: str = "来源") -> str:
    response = client.post(
        "/api/sources",
        json={
            "name": name,
            "url": url,
            "platform": "",
            "language": "auto",
            "category_id": CATEGORY_ID,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def configure_model(client: TestClient) -> None:
    provider = client.post(
        "/api/llm/providers", json={"name": "primary", "base_url": "https://llm.test/v1"}
    )
    provider_id = provider.json()["id"]
    assert (
        client.post(
            f"/api/llm/providers/{provider_id}/models", json={"model": "test-model"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/llm/providers/{provider_id}/keys", json={"secret": "secret-main"}
        ).status_code
        == 201
    )


def prompts(client: TestClient, task_kind: str | None = None) -> list[dict[str, object]]:
    params = {"task_kind": task_kind} if task_kind else None
    response = client.get("/api/llm/prompts", params=params)
    assert response.status_code == 200, response.text
    return list(response.json())


#: 能通过编译的翻译模板；新建与就地编辑共用，避免测试之间的模板漂移。
GOOD_TEMPLATE = "输出 title、summary、content：{{title}} {{summary}} {{content}}"


def create_prompt(client: TestClient, **overrides: object) -> dict[str, object]:
    """新建一个提示词版本，默认是翻译任务的合法草稿。"""

    body: dict[str, object] = {
        "task_kind": "translate_message",
        "prompt_key": TRANSLATE_KEY,
        "name": "翻译草稿",
        "user_template": GOOD_TEMPLATE,
    }
    body.update(overrides)
    response = client.post("/api/llm/prompts", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def edit_body(**overrides: object) -> dict[str, object]:
    """就地编辑请求体；字段齐全，避免漏传字段掩盖被拒绝的真实原因。"""

    body: dict[str, object] = {
        "name": "改过的名字",
        "system_template": "只返回 JSON。",
        "user_template": GOOD_TEMPLATE,
        "note": "就地改了文本",
    }
    body.update(overrides)
    return body


def usage_of(client: TestClient, prompt_id: str) -> dict[str, object]:
    response = client.get(f"/api/llm/prompts/{prompt_id}/usage")
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_seeded_prompts_keep_existing_version_strings(client: TestClient) -> None:
    """迁移种子必须与改造前的版本串一致，否则历史译文与幂等键对不上。"""

    seeded = {(item["prompt_key"], item["version_string"]) for item in prompts(client)}
    assert (TRANSLATE_KEY, "translation-v1") in seeded
    assert (ENRICH_KEY, "enrich-v1") in seeded
    for item in prompts(client):
        assert item["status"] == "active"


def test_compile_reports_unknown_placeholder_and_missing_output_field(client: TestClient) -> None:
    """编译只提示不落库：未知占位符、缺少输出字段都是错误。"""

    bad = client.post(
        "/api/llm/prompts/compile",
        json={
            "task_kind": "translate_message",
            "system_template": "",
            "user_template": "翻译 {{unknown}} 并输出 title、summary、content",
        },
    )
    assert bad.status_code == 200
    body = bad.json()
    assert body["ok"] is False
    assert any("unknown" in item["message"] for item in body["errors"])

    missing = client.post(
        "/api/llm/prompts/compile",
        json={
            "task_kind": "translate_message",
            "user_template": "只翻译标题：{{title}}",
        },
    )
    assert missing.json()["ok"] is False
    assert any("content" in item["message"] for item in missing.json()["errors"])

    good = client.post(
        "/api/llm/prompts/compile",
        json={
            "task_kind": "translate_message",
            "system_template": "你是翻译，只返回 JSON。",
            "user_template": (
                "把 title、summary、content 翻译成中文：{{title}} / {{summary}} / {{content}}"
            ),
            "sample": {"title": "A", "summary": "B", "content": "C"},
        },
    )
    assert good.json()["ok"] is True
    assert good.json()["user"] == "把 title、summary、content 翻译成中文：A / B / C"
    assert good.json()["variables"] == ["title", "summary", "content"]


def test_new_version_increments_and_activate_archives_others(client: TestClient) -> None:
    """新建版本号递增；启用后同任务类型的旧版本自动归档。"""

    created = client.post(
        "/api/llm/prompts",
        json={
            "task_kind": "translate_message",
            "prompt_key": TRANSLATE_KEY,
            "name": "翻译成中文 v2",
            "user_template": "输出 title、summary、content：{{title}} {{summary}} {{content}}",
            "note": "更短的中文提示",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 2
    assert created.json()["status"] == "draft"

    activated = client.post(f"/api/llm/prompts/{created.json()['id']}/activate")
    assert activated.json()["status"] == "active"
    kinds = {item["id"]: item["status"] for item in prompts(client, "translate_message")}
    assert kinds[created.json()["id"]] == "active"
    assert [item["status"] for item in prompts(client, "translate_message")].count("active") == 1

    invalid = client.post(
        "/api/llm/prompts",
        json={
            "task_kind": "translate_message",
            "prompt_key": TRANSLATE_KEY,
            "user_template": "缺少输出字段声明",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "prompt_invalid"


@respx.mock
def test_source_task_setting_disables_and_binds_prompt(client: TestClient) -> None:
    """来源可以关掉某类任务，也可以绑定自己的提示词；只改开关不动提示词。"""

    container: Container = client.app.state.container
    respx.get("https://prompt.test/feed").mock(
        return_value=Response(200, content=feed("Long english headline", LONG_ENGLISH_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://prompt.test/feed")

    default_view = client.get(f"/api/task-settings/source/{source_id}").json()
    assert [item["task_kind"] for item in default_view] == ["translate_message", "enrich_message"]
    assert all(item["scope"] == "default" and item["effective_enabled"] for item in default_view)

    custom = client.post(
        "/api/llm/prompts",
        json={
            "task_kind": "enrich_message",
            "prompt_key": "enrich-short",
            "name": "短摘要",
            "user_template": "输出 title、summary、keywords：{{title}} {{summary}} {{content}}",
        },
    ).json()
    saved = client.put(
        f"/api/task-settings/source/{source_id}",
        json={
            "settings": [
                {"task_kind": "translate_message", "enabled": False},
                {"task_kind": "enrich_message", "prompt_id": custom["id"]},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    views = {item["task_kind"]: item for item in saved.json()}
    assert views["translate_message"]["effective_enabled"] is False
    # 只设了开关的条目仍继承全局提示词，只设了提示词的条目仍启用。
    assert views["translate_message"]["effective_prompt_version"] == "translation-v1"
    assert views["enrich_message"]["effective_enabled"] is True
    assert views["enrich_message"]["effective_prompt_version"] == "enrich-short-v1"
    assert views["enrich_message"]["scope"] == "source"

    run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert run.status_code == 202
    worker = Worker(container)
    assert worker.run_once() is True
    queued = client.get("/api/tasks", params={"status": "queued"}).json()
    assert [task["task_type"] for task in queued] == ["enrich_message"]

    container.enrichment_service.provider = FakeProvider()
    assert worker.run_once() is True
    message_id = client.get("/api/messages").json()[0]["id"]
    version = client.get(f"/api/messages/{message_id}").json()["versions"][0]
    assert version["enrichments"][0]["prompt_version"] == "enrich-short-v1"


@respx.mock
def test_category_default_is_overridden_by_source(client: TestClient) -> None:
    """分类默认生效，来源可以覆盖；来源没有条目时用分类设置。"""

    container: Container = client.app.state.container
    respx.get("https://inherit.test/feed").mock(
        return_value=Response(200, content=feed("Long english headline", LONG_ENGLISH_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://inherit.test/feed")

    custom = client.post(
        "/api/llm/prompts",
        json={
            "task_kind": "enrich_message",
            "prompt_key": "enrich-category",
            "name": "分类默认摘要",
            "user_template": "输出 title、summary、keywords：{{title}} {{summary}} {{content}}",
        },
    ).json()
    saved = client.put(
        f"/api/task-settings/category/{CATEGORY_ID}",
        json={
            "settings": [
                {"task_kind": "translate_message", "enabled": False},
                {"task_kind": "enrich_message", "prompt_id": custom["id"]},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    views = {item["task_kind"]: item for item in saved.json()}
    # 只关开关的条目仍继承全局提示词；scope 表示生效提示词来自哪一层。
    assert views["translate_message"]["effective_enabled"] is False
    assert views["translate_message"]["scope"] == "default"
    assert views["enrich_message"]["scope"] == "category"
    assert views["enrich_message"]["effective_prompt_version"] == "enrich-category-v1"

    # 来源没有自己的条目，继承分类：英文消息不再自动翻译。
    inherited = client.get(f"/api/task-settings/source/{source_id}").json()
    assert {item["task_kind"]: item["effective_enabled"] for item in inherited} == {
        "translate_message": False,
        "enrich_message": True,
    }

    # 来源显式打开后覆盖分类。
    client.put(
        f"/api/task-settings/source/{source_id}",
        json={"settings": [{"task_kind": "translate_message", "enabled": True}]},
    )
    assert client.post("/api/collection/runs", json={"source_ids": [source_id]}).status_code == 202
    worker = Worker(container)
    assert worker.run_once() is True
    queued = client.get("/api/tasks", params={"status": "queued"}).json()
    assert sorted(task["task_type"] for task in queued) == ["enrich_message", "translate_message"]


def test_prompt_test_writes_audit_but_no_result(client: TestClient) -> None:
    """试跑真实调用并写审计，但不写译文或加工结果；失败保留原因。"""

    container: Container = client.app.state.container
    configure_model(client)
    source_id = create_source(client, "https://sample.test/feed")
    prompt_id = prompts(client, "translate_message")[0]["id"]

    fake = FakeProvider()
    container.prompt_service.provider = fake
    tested = client.post(
        f"/api/llm/prompts/{prompt_id}/test",
        json={"sample": {"title": "Title", "summary": "Summary", "content": "Body"}},
    )
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] is True and body["output"]["title"] == "中文标题"
    assert body["latency_ms"] == 7 and body["key_masked"].endswith("main")
    assert "Title" in body["user"] and body["user"] in fake.prompts[0].user

    connection = sqlite3.connect(container.settings.database_path)
    connection.row_factory = sqlite3.Row
    calls = connection.execute(
        "SELECT prompt_version, task_id FROM llm_calls ORDER BY created_at DESC"
    ).fetchall()
    connection.close()
    assert calls[0]["prompt_version"] == "translation-v1+prompt-test"
    assert calls[0]["task_id"] is None
    # 试跑不产生结果记录，消息也不存在。
    assert client.get("/api/messages").json() == []

    failing = FakeProvider({"secret-main": "rate_limited"})
    container.prompt_service.provider = failing
    failed = client.post(
        f"/api/llm/prompts/{prompt_id}/test",
        json={"sample": {"title": "T", "summary": "S", "content": "C"}},
    ).json()
    assert failed["ok"] is False and failed["error_code"] == "rate_limited"
    assert failed["output"] == {}

    missing = client.post(f"/api/llm/prompts/{prompt_id}/test", json={})
    assert missing.status_code == 400
    assert missing.json()["code"] == "sample_required"
    assert source_id


def test_new_prompt_key_starts_at_v1_and_bad_key_is_rejected(client: TestClient) -> None:
    """新建全新提示词从 v1 开始，任务类型取自 specs；非法业务键直接拒绝。"""

    created = create_prompt(client, prompt_key="news-brief", name="简报")
    assert created["version"] == 1
    assert created["version_string"] == "news-brief-v1"
    assert created["status"] == "draft"
    kinds = {item["task_kind"] for item in client.get("/api/llm/prompts/specs").json()}
    assert created["task_kind"] in kinds

    for bad in ("News-Brief", "news:brief"):
        rejected = client.post(
            "/api/llm/prompts",
            json={
                "task_kind": "translate_message",
                "prompt_key": bad,
                "user_template": GOOD_TEMPLATE,
            },
        )
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["code"] == "prompt_key_invalid"


def test_draft_is_editable_in_place_until_it_is_used(client: TestClient) -> None:
    """未被引用的草稿可以就地改文本；试跑写下的审计同样让它锁定。"""

    container: Container = client.app.state.container
    configure_model(client)
    prompt = create_prompt(client)
    prompt_id = str(prompt["id"])
    assert usage_of(client, prompt_id) == {
        "used": False,
        "calls": 0,
        "results": 0,
        "tasks": 0,
        "bindings": 0,
    }

    edited = client.patch(f"/api/llm/prompts/{prompt_id}", json=edit_body())
    assert edited.status_code == 200, edited.text
    # 版本串与状态不变，只有文本和 updated_at 变；改完再读一致。
    assert edited.json()["version_string"] == prompt["version_string"]
    assert edited.json()["status"] == "draft"
    fetched = client.get(f"/api/llm/prompts/{prompt_id}").json()
    assert fetched["name"] == "改过的名字"
    assert fetched["system_template"] == "只返回 JSON。"
    assert fetched["user_template"] == GOOD_TEMPLATE
    assert fetched["note"] == "就地改了文本"

    container.prompt_service.provider = FakeProvider()
    tested = client.post(
        f"/api/llm/prompts/{prompt_id}/test",
        json={"sample": {"title": "Title", "summary": "Summary", "content": "Body"}},
    )
    assert tested.status_code == 200, tested.text
    used = usage_of(client, prompt_id)
    assert used["used"] is True and used["calls"] == 1

    blocked = client.patch(f"/api/llm/prompts/{prompt_id}", json=edit_body(note="第二次改"))
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["code"] == "prompt_used"
    removed = client.delete(f"/api/llm/prompts/{prompt_id}")
    assert removed.status_code == 400, removed.text
    assert removed.json()["code"] == "prompt_used"


def test_invalid_edit_is_rejected_and_keeps_original_text(client: TestClient) -> None:
    """就地编辑同样要过编译校验；不通过时库里保持原文本。"""

    prompt = create_prompt(client, prompt_key="validate-me", name="校验")
    rejected = client.patch(
        f"/api/llm/prompts/{prompt['id']}",
        json=edit_body(user_template="缺少输出字段声明"),
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "prompt_invalid"
    assert client.get(f"/api/llm/prompts/{prompt['id']}").json()["user_template"] == GOOD_TEMPLATE


def test_delete_only_removes_unused_non_active_versions(client: TestClient) -> None:
    """删除规则：启用中的版本先换一个，未使用且非启用的可以真删。"""

    seeded = next(
        item for item in prompts(client, "translate_message") if item["status"] == "active"
    )
    blocked = client.delete(f"/api/llm/prompts/{seeded['id']}")
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["code"] == "prompt_active"

    draft = create_prompt(client, prompt_key="delete-me", name="待删")
    removed = client.delete(f"/api/llm/prompts/{draft['id']}")
    assert removed.status_code == 204, removed.text
    assert {item["id"] for item in prompts(client, "translate_message")} == {seeded["id"]}

    # 换个启用版本后旧版本自动归档，就不再被"正在启用"挡住。
    takeover = create_prompt(client, prompt_key="takeover", name="接管")
    assert client.post(f"/api/llm/prompts/{takeover['id']}/activate").json()["status"] == "active"
    assert client.get(f"/api/llm/prompts/{seeded['id']}").json()["status"] == "archived"
    assert client.delete(f"/api/llm/prompts/{seeded['id']}").status_code == 204
    assert {item["id"] for item in prompts(client, "translate_message")} == {takeover["id"]}


@respx.mock
def test_binding_blocks_edit_and_delete_without_any_call(client: TestClient) -> None:
    """来源绑定该版本后，即使还没有调用记录也不能改文本或删除。"""

    respx.get("https://bind.test/feed").mock(
        return_value=Response(200, content=feed("Long english headline", LONG_ENGLISH_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://bind.test/feed")
    prompt = create_prompt(client, prompt_key="bind-check", name="绑定检查")
    saved = client.put(
        f"/api/task-settings/source/{source_id}",
        json={"settings": [{"task_kind": "translate_message", "prompt_id": prompt["id"]}]},
    )
    assert saved.status_code == 200, saved.text

    usage = usage_of(client, str(prompt["id"]))
    assert usage["bindings"] == 1 and usage["used"] is True
    blocked = client.patch(f"/api/llm/prompts/{prompt['id']}", json=edit_body())
    assert blocked.status_code == 400 and blocked.json()["code"] == "prompt_used"
    assert client.delete(f"/api/llm/prompts/{prompt['id']}").status_code == 400


@respx.mock
def test_finished_task_and_result_block_edit(client: TestClient) -> None:
    """跑过一次真实任务后，任务快照与结果都指向该版本，因此不能再改。"""

    container: Container = client.app.state.container
    respx.get("https://used.test/feed").mock(
        return_value=Response(200, content=feed("Long english headline", LONG_ENGLISH_SUMMARY))
    )
    configure_model(client)
    source_id = create_source(client, "https://used.test/feed")
    prompt = create_prompt(client, prompt_key="used-check", name="使用检查")
    saved = client.put(
        f"/api/task-settings/source/{source_id}",
        json={
            "settings": [
                {"task_kind": "translate_message", "prompt_id": prompt["id"]},
                {"task_kind": "enrich_message", "enabled": False},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    container.translation_service.provider = FakeProvider()

    run = client.post("/api/collection/runs", json={"source_ids": [source_id]})
    assert run.status_code == 202, run.text
    worker = Worker(container)
    assert worker.run_once() is True
    assert worker.run_once() is True

    usage = usage_of(client, str(prompt["id"]))
    assert usage["tasks"] >= 1 and usage["results"] >= 1 and usage["calls"] >= 1
    blocked = client.patch(f"/api/llm/prompts/{prompt['id']}", json=edit_body())
    assert blocked.status_code == 400 and blocked.json()["code"] == "prompt_used"


def test_usage_matches_version_string_exactly(client: TestClient) -> None:
    """使用统计按版本串等值匹配：v10 的调用不能算到 v1 头上。"""

    container: Container = client.app.state.container
    configure_model(client)
    first = create_prompt(client, prompt_key="prefix-check", name="第一版")
    latest = first
    for _ in range(9):
        latest = create_prompt(client, prompt_key="prefix-check", name="后续版本")
    assert first["version_string"] == "prefix-check-v1"
    assert latest["version_string"] == "prefix-check-v10"

    container.prompt_service.provider = FakeProvider()
    tested = client.post(
        f"/api/llm/prompts/{latest['id']}/test",
        json={"sample": {"title": "Title", "summary": "Summary", "content": "Body"}},
    )
    assert tested.status_code == 200, tested.text

    assert usage_of(client, str(first["id"]))["calls"] == 0
    assert usage_of(client, str(latest["id"]))["calls"] == 1
    # v1 确实没被引用过，所以仍可编辑。
    assert client.patch(f"/api/llm/prompts/{first['id']}", json=edit_body()).status_code == 200
