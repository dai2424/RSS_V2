"""线索实体：创建、成员增删、版本锁定、级联与上限。"""

from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Response
from keyword_support import LONG_TEXT, collect, create_source, feed

#: 三条消息；按发布时间倒序，种子函数返回的列表顺序与列表页一致。
FEED = [
    ("one", "第一条消息", "Sat, 10 Oct 2026 00:00:00 GMT"),
    ("two", "第二条消息", "Fri, 09 Oct 2026 00:00:00 GMT"),
    ("three", "第三条消息", "Thu, 08 Oct 2026 00:00:00 GMT"),
]


def seeded(client: TestClient, url: str = "https://leads.test/feed") -> list[str]:
    """造三条消息，返回消息 id（按发布时间从新到旧）。"""

    respx.get(url).mock(return_value=Response(200, content=feed(FEED)))
    source_id = create_source(client, url)
    collect(client, source_id)
    items = client.get("/api/messages", params={"limit": "50"}).json()["items"]
    return [str(item["id"]) for item in items]


def create(client: TestClient, title: str, message_ids: list[str]) -> dict[str, Any]:
    response = client.post("/api/leads", json={"title": title, "message_ids": message_ids})
    assert response.status_code == 201, response.text
    return dict(response.json())


def detail(client: TestClient, lead_id: str) -> dict[str, Any]:
    response = client.get(f"/api/leads/{lead_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


@respx.mock
def test_create_lead_locks_versions_and_lists(client: TestClient) -> None:
    """建线索锁定当前最新版本；列表给出成员条数，详情按加入时间返回成员。"""

    ids = seeded(client)
    lead = create(client, "同一件事", ids[:2])
    assert (lead["members"], lead["note"]) == (2, None)

    listed = client.get("/api/leads").json()
    assert (listed["total"], listed["items"][0]["id"]) == (1, lead["id"])

    body = detail(client, lead["id"])
    # 成员按发布时间从早到晚：FEED 是从新到旧，因此期望顺序整体反转。
    assert [member["message_id"] for member in body["members"]] == list(reversed(ids[:2]))
    # 成员锁的是版本：三条消息都还没有第二个版本。
    assert [member["version_number"] for member in body["members"]] == [1, 1]
    assert all(member["source_name"] for member in body["members"])


@respx.mock
def test_single_message_lead_and_empty_rejected(client: TestClient) -> None:
    """单篇报道可以成线索（阶段 4 允许单篇选题）；空成员与无效消息仍被拒绝。"""

    ids = seeded(client)
    single = client.post("/api/leads", json={"title": "只有一条", "message_ids": ids[:1]})
    assert single.status_code == 201
    assert single.json()["members"] == 1
    repeated = client.post(
        "/api/leads", json={"title": "重复同一条", "message_ids": [ids[0], ids[0]]}
    )
    assert repeated.status_code == 201
    assert repeated.json()["members"] == 1
    empty = client.post("/api/leads", json={"title": "没有成员", "message_ids": []})
    assert empty.status_code == 422
    missing = client.post("/api/leads", json={"title": "消息不存在", "message_ids": ["nope"]})
    assert (missing.status_code, missing.json()["code"]) == (404, "message_not_found")


@respx.mock
def test_member_changes_are_idempotent_and_limited(client: TestClient) -> None:
    """追加成员幂等；超过上限明确拒绝并说明怎么继续。"""

    items = [
        (f"m{index}", f"第 {index} 条", "Sat, 10 Oct 2026 00:00:00 GMT") for index in range(22)
    ]
    respx.get("https://leads-many.test/feed").mock(return_value=Response(200, content=feed(items)))
    source_id = create_source(client, "https://leads-many.test/feed")
    collect(client, source_id)
    ids = [
        str(item["id"])
        for item in client.get("/api/messages", params={"limit": "50"}).json()["items"]
    ]
    lead = create(client, "很多条", ids[:19])

    again = client.post(f"/api/leads/{lead['id']}/messages", json={"message_ids": ids[:2]})
    assert (again.status_code, again.json()["added"]) == (200, 0)
    # 19 + 1 = 20 正好到上限。
    one = client.post(f"/api/leads/{lead['id']}/messages", json={"message_ids": [ids[19]]})
    assert (one.status_code, one.json()["lead"]["members"]) == (200, 20)
    over = client.post(f"/api/leads/{lead['id']}/messages", json={"message_ids": [ids[20]]})
    assert (over.status_code, over.json()["code"]) == (400, "lead_members_limit")
    assert "最多 20 条消息" in over.json()["message"]


@respx.mock
def test_remove_and_delete(client: TestClient) -> None:
    """移除成员按版本 id；删线索后详情 404，消息本身不动。"""

    ids = seeded(client)
    lead = create(client, "临时线索", ids[:2])
    version_id = detail(client, lead["id"])["members"][0]["version_id"]

    removed = client.delete(f"/api/leads/{lead['id']}/messages/{version_id}")
    assert (removed.status_code, removed.json()["removed"]) == (200, 1)
    again = client.delete(f"/api/leads/{lead['id']}/messages/{version_id}")
    assert (again.status_code, again.json()["code"]) == (404, "lead_member_not_found")

    assert client.delete(f"/api/leads/{lead['id']}").json() == {"deleted": 1}
    assert client.get(f"/api/leads/{lead['id']}").status_code == 404
    # 消息与它的加工结果不受影响。
    assert client.get("/api/messages", params={"limit": "50"}).json()["total"] == 3


@respx.mock
def test_deleting_message_removes_it_from_lead(client: TestClient) -> None:
    """消息被删掉后不再留在线索里（成员随版本级联）。"""

    ids = seeded(client, "https://leads-cascade.test/feed")
    lead = create(client, "会少一条", ids[:2])
    assert client.delete(f"/api/messages/{ids[0]}").status_code in (200, 204)

    body = detail(client, lead["id"])
    assert [member["message_id"] for member in body["members"]] == [ids[1]]


@respx.mock
def test_member_keeps_its_version_when_message_updates(client: TestClient) -> None:
    """成员锁定加入时的版本：消息出现新版本后，成员仍指向旧版本。"""

    first = feed(FEED)
    changed = feed(FEED, text="更新：" + LONG_TEXT)  # 正文变了才会产生第二个版本
    respx.get("https://leads-version.test/feed").mock(
        side_effect=[Response(200, content=first), Response(200, content=changed)]
    )
    source_id = create_source(client, "https://leads-version.test/feed")
    collect(client, source_id)
    ids = [
        str(item["id"])
        for item in client.get("/api/messages", params={"limit": "50"}).json()["items"]
    ]
    lead = create(client, "版本会变", ids[:2])
    locked = [member["version_id"] for member in detail(client, lead["id"])["members"]]

    collect(client, source_id)
    latest = client.get(f"/api/messages/{ids[0]}").json()["versions"][0]
    assert latest["version_number"] == 2  # 消息确实有了新版本
    body = detail(client, lead["id"])
    assert [member["version_id"] for member in body["members"]] == locked
    assert [member["version_number"] for member in body["members"]] == [1, 1]
    # 再次加入这条消息会被跳过（成员已经包含它）。
    added = client.post(f"/api/leads/{lead['id']}/messages", json={"message_ids": [ids[0]]})
    assert added.json()["added"] == 0
