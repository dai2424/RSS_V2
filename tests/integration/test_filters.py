"""消息处理状态与任务筛选的集成测试：口径与列表/总数同源。"""

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
    stub_providers,
)

from rss_v2.bootstrap import Container
from rss_v2.domain import ExternalServiceError, Keyword, KeywordKind


class FailingEnrich(KeywordProvider):
    """加工永远失败，用来构造"加工失败"状态。"""

    def enrich(self, provider, model, secret, prompt, prompt_version):  # type: ignore[no-untyped-def]
        raise ExternalServiceError("llm_invalid_output", "模拟输出不合规")


def titles(rows: list[dict[str, object]]) -> list[str]:
    """排序后比较：同发布时间的消息按 id 兜底排序，与业务语义无关。"""

    return sorted(str(item["latest_version"]["title"]) for item in rows)  # type: ignore[index]


def messages(client: TestClient, **params: str) -> tuple[list[str], int]:
    page = client.get("/api/messages", params=params).json()
    return titles(page["items"]), int(page["total"])


def task_totals(client: TestClient, **params: str) -> tuple[int, int]:
    page = client.get("/api/tasks", params=params).json()
    return len(page["items"]), int(page["total"])


def seed_states(client: TestClient) -> dict[str, str]:
    """造出四种状态：已加工（含已翻译）、未加工、加工失败。返回来源 id。"""

    respx.get("https://done.test/feed").mock(
        return_value=Response(
            200, content=feed([("done", "加工成功的那条", "Thu, 08 Oct 2026 00:00:00 GMT")])
        )
    )
    respx.get("https://english.test/feed").mock(
        return_value=Response(
            200,
            content=feed(
                [("en", "Translated entry", "Thu, 08 Oct 2026 00:00:00 GMT")],
                "A long English description for translation and enrichment. " * 10,
            ),
        )
    )
    respx.get("https://none.test/feed").mock(
        return_value=Response(
            200, content=feed([("none", "还没加工的那条", "Wed, 07 Oct 2026 00:00:00 GMT")])
        )
    )
    respx.get("https://fail.test/feed").mock(
        return_value=Response(
            200, content=feed([("fail", "加工失败的那条", "Tue, 06 Oct 2026 00:00:00 GMT")])
        )
    )
    configure_model(client)
    stub_providers(client, KeywordProvider(default=(Keyword("计算平台", KeywordKind.ENTITY),)))
    ids = {
        "done": create_source(client, "https://done.test/feed", "done"),
        "english": create_source(client, "https://english.test/feed", "english"),
    }
    collect(client, ids["done"])
    collect(client, ids["english"])
    # 未加工：来源关掉加工开关后再采集。
    ids["none"] = create_source(client, "https://none.test/feed", "none")
    disable_enrich(client, ids["none"])
    collect(client, ids["none"])
    drain_worker(client)

    # 加工失败：换成会失败的 provider 之后再采集。外部服务错误会带着 60 秒退避回到
    # queued（等自动重试），因此这条任务在测试期间稳定处于"排队中"，加工结果则是 failed。
    stub_providers(client, FailingEnrich(default=(Keyword("计算平台", KeywordKind.ENTITY),)))
    ids["fail"] = create_source(client, "https://fail.test/feed", "fail")
    collect(client, ids["fail"])
    drain_worker(client)
    return ids


def insert_failed_task(client: TestClient) -> None:
    """直接写一条失败任务，把"失败任务"这个筛选维度与重试机制解耦。"""

    container: Container = client.app.state.container
    with container.database.transaction() as connection:
        connection.execute(
            "INSERT INTO tasks(id,task_type,idempotency_key,status,attempts,payload_json,"
            "created_at,updated_at,available_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("failed-task", "enrich_message", "failed-task-key", "failed", 3, "{}", 1, 1, 0),
        )


def message_id(client: TestClient, title: str) -> str:
    return str(
        next(
            item["id"]
            for item in client.get("/api/messages").json()["items"]
            if item["latest_version"]["title"] == title
        )
    )


@respx.mock
def test_message_state_filter(client: TestClient) -> None:
    """处理状态筛选：每种状态只命中该状态的条目，总数与列表同源。"""

    seed_states(client)

    # 给"还没加工"的那条排一个任务，制造"有任务在排队"。
    client.post(f"/api/messages/{message_id(client, '还没加工的那条')}/enrich")

    assert messages(client, state="enriched") == (["Translated entry", "加工成功的那条"], 2)
    assert messages(client, state="translated") == (["Translated entry"], 1)
    assert messages(client, state="untranslated") == (
        ["加工失败的那条", "加工成功的那条", "还没加工的那条"],
        3,
    )
    assert messages(client, state="unenriched") == (["加工失败的那条", "还没加工的那条"], 2)
    # 加工失败与从未加工是两回事。
    assert messages(client, state="enrich_failed") == (["加工失败的那条"], 1)
    # 排队中：刚入队的那条，以及加工失败后带着退避等待重试的那条。
    assert messages(client, state="pending") == (["加工失败的那条", "还没加工的那条"], 2)

    assert messages(client)[1] == 4
    # 非法状态由接口层拒绝。
    assert client.get("/api/messages", params={"state": "whatever"}).status_code == 422


@respx.mock
def test_task_filters_and_totals(client: TestClient) -> None:
    """任务筛选：类型、来源、目标搜索与创建时间范围，总数与列表同源。"""

    ids = seed_states(client)

    assert task_totals(client, task_type="collect_source")[1] == 4
    assert task_totals(client, task_type="translate_message")[1] == 1
    # 采集任务按快照归属来源；加工任务按消息所属来源。
    assert task_totals(client, task_type="collect_source", source_id=ids["done"]) == (1, 1)
    assert task_totals(client, task_type="enrich_message", source_id=ids["fail"]) == (1, 1)
    # 目标搜索：来源名与消息标题都能命中。
    assert task_totals(client, q="fail")[1] >= 1
    assert task_totals(client, q="加工失败的那条")[1] == 1
    # 创建时间范围。
    assert task_totals(client, since="99999999999")[1] == 0
    assert task_totals(client, until="1")[1] == 0
    # 组合筛选：直接写一条失败任务，避免依赖 worker 的重试次数。
    insert_failed_task(client)
    assert task_totals(client, status="failed", task_type="enrich_message") == (1, 1)
    # 加工失败后等待重试的那条处于排队状态。
    assert task_totals(client, status="queued", task_type="enrich_message")[1] == 1
    # 列表与总数同源：无筛选时两者一致（一页装得下）。
    assert task_totals(client)[0] == task_totals(client)[1]
