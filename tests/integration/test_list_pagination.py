"""列表分页契约：items 与 total 同源，翻页窗口不改变总数。

总数由计数查询给出，列表由分页查询给出；两处共用同一套过滤条件。这里验证
总数不会被 limit/offset 影响，也不会漏掉筛选条件。
"""

from __future__ import annotations

from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import collect, create_source, feed

MESSAGES = [
    ("first", "第一条消息", "Mon, 05 Oct 2026 00:00:00 GMT"),
    ("second", "第二条消息", "Tue, 06 Oct 2026 00:00:00 GMT"),
    ("third", "第三条消息", "Wed, 07 Oct 2026 00:00:00 GMT"),
]


def three_messages(client: TestClient) -> list[dict[str, Any]]:
    """采集三条发布时间不同的消息，返回默认（不传筛选）的列表。"""

    url = "https://pagination.test/feed"
    with respx.mock:
        respx.get(url).mock(return_value=Response(200, content=feed(MESSAGES)))
        source_id = create_source(client, url)
        collect(client, source_id)
    response = client.get("/api/messages")
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


@respx.mock
def test_message_total_ignores_page_window(client: TestClient) -> None:
    """总数是过滤后的全量；翻到第几页、每页几条都不影响它。"""

    items = three_messages(client)
    assert len(items) == 3
    assert client.get("/api/messages").json()["total"] == 3

    first_page = client.get("/api/messages", params={"limit": 2, "offset": 0}).json()
    assert len(first_page["items"]) == 2
    assert first_page["total"] == 3

    last_page = client.get("/api/messages", params={"limit": 2, "offset": 2}).json()
    assert len(last_page["items"]) == 1
    assert last_page["total"] == 3
    assert last_page["items"][0]["id"] == items[2]["id"]

    # 偏移越过末尾：这一页没有内容，但总数仍然是可信的。
    beyond = client.get("/api/messages", params={"offset": 50}).json()
    assert beyond["items"] == []
    assert beyond["total"] == 3


@respx.mock
def test_message_total_follows_filters(client: TestClient) -> None:
    """总数与列表用同一套筛选：来源、全文、时间范围都要反映到总数上。"""

    items = three_messages(client)
    published = sorted(item["latest_version"]["published_at"] for item in items)

    matched = client.get("/api/messages", params={"q": "第二条"}).json()
    assert matched["total"] == 1
    assert [item["latest_version"]["title"] for item in matched["items"]] == ["第二条消息"]

    # 只保留发布时间不早于中间那条：发布时间各不相同，因此恰好是两条。
    recent = client.get("/api/messages", params={"since": published[1]}).json()
    assert recent["total"] == 2

    older = client.get("/api/messages", params={"until": published[0]}).json()
    assert older["total"] == 1

    missing = client.get("/api/messages", params={"source_id": "不存在的来源"}).json()
    assert missing["items"] == []
    assert missing["total"] == 0


@respx.mock
def test_task_total_follows_status(client: TestClient) -> None:
    """任务总数按状态过滤，且与各状态相加一致。"""

    three_messages(client)
    all_tasks = client.get("/api/tasks").json()
    assert all_tasks["total"] == len(all_tasks["items"])
    assert all_tasks["total"] > 0

    statuses = ["queued", "running", "succeeded", "failed"]
    totals: list[int] = []
    for status in statuses:
        page = client.get("/api/tasks", params={"status": status, "limit": 100}).json()
        assert page["total"] == len(page["items"])
        totals.append(page["total"])
    assert sum(totals) == all_tasks["total"]

    # 状态筛选同样作用在总数上：四种状态互斥，取交集必然为空。
    empty = client.get("/api/tasks", params={"status": "queued", "offset": 100}).json()
    assert empty["items"] == []
    assert empty["total"] == totals[0]
