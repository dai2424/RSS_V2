"""资料准备的纯辅助函数：URL 规范化、检索式筛选、候选交错、资料行构造与摘要序列化。

这些函数只依赖领域对象，不碰数据库与网络；放在单独模块里便于单测，也让用例层与
执行器都能复用同一套口径（例如"什么算同一地址"只有一份实现）。
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

from rss_v2.domain import (
    BUDGET_EXHAUSTED,
    LeadMember,
    Material,
    MaterialKind,
    MaterialQueries,
    MaterialQuery,
    MaterialStatus,
    MaterialSummary,
    MessageVersion,
    SearchHit,
    Task,
)
from rss_v2.domain.values import new_id

#: 去重时忽略的跟踪参数：同一篇文章带不同 utm 参数不该读两遍。
_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "spm", "from")

#: 摘要里最多保留几条缺口说明。
MAX_NOTES = 6

#: 一次运行最多考虑多少条检索候选：再多也只是排在预算之外，徒增占位行。
MAX_CANDIDATES_PER_RUN = 40

#: 预算耗尽时为多少条候选留"未读取"占位行；其余只计入摘要的数字。
MAX_SKIPPED_ROWS = 8


def pick_queries(result: MaterialQueries, limit: int) -> list[MaterialQuery]:
    """归一化检索式：去空、去重、按预算截断。"""

    picked: list[MaterialQuery] = []
    seen: set[str] = set()
    for item in result.queries:
        text = " ".join(item.text.split())
        if not text or text in seen:
            continue
        seen.add(text)
        picked.append(MaterialQuery(text=text[:120], purpose=" ".join(item.purpose.split())[:120]))
        if len(picked) >= max(1, limit):
            break
    return picked


def interleave_hits(queues: Sequence[Sequence[SearchHit]], limit: int) -> list[SearchHit]:
    """按"每条检索式轮流取一条"合并候选，最多取 limit 条。

    这样预算不够时每条检索式都有机会被读到，而不是第一条检索式把额度用光。
    """

    merged: list[SearchHit] = []
    depth = 0
    while len(merged) < limit:
        progressed = False
        for queue in queues:
            if depth < len(queue):
                merged.append(queue[depth])
                progressed = True
                if len(merged) >= limit:
                    break
        if not progressed:
            break
        depth += 1
    return merged


def members_text(members: Sequence[LeadMember]) -> str:
    lines: list[str] = []
    for index, member in enumerate(members, start=1):
        published = beijing_time(member.published_at or member.collected_at)
        lines.append(
            f"{index}. {member.title or '（无标题）'}（来源：{member.source_name}，{published}）"
        )
    return "\n".join(lines)


def beijing_time(timestamp: int) -> str:
    return datetime.datetime.fromtimestamp(
        timestamp, datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M")


def weak_row(row: Material) -> bool:
    """这条资料没有证据价值（失败/不可用/预算未读），可以被后续读取替换。

    "读到正文/仅摘要"的行不替换：历史文章依据不能被悄然换成最新正文。
    """

    return row.status in {MaterialStatus.FAILED.value, MaterialStatus.UNAVAILABLE.value} or (
        row.error_code == BUDGET_EXHAUSTED
    )


def normalize_url(url: str) -> str:
    """规范化 URL：去 fragment、主机小写、去掉跟踪参数；只保留 http/https。"""

    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair and not pair.split("=", 1)[0].lower().startswith(_TRACKING_PREFIXES)
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ""))


def snapshot_hash(members: Sequence[LeadMember]) -> str:
    digest = hashlib.sha256()
    for member in members:
        digest.update(f"{member.message_id}:{member.version_id}\n".encode())
    return digest.hexdigest()[:24]


def material_from_version(
    project_id: str,
    task: Task,
    version: MessageVersion,
    status: str,
    text: str,
    timestamp: int,
) -> Material:
    return Material(
        id=new_id(),
        project_id=project_id,
        kind=MaterialKind.RSS.value,
        status=status,
        url=version.url,
        final_url="",
        title=version.title,
        publisher="",
        published_at=version.published_at,
        fetched_at=timestamp,
        content_text=text,
        content_hash=hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
        if text
        else "",
        byte_size=len(text.encode("utf-8")),
        truncated=False,
        message_version_id=version.id,
        task_id=task.id,
        error_code="" if text else "message_content_empty",
        error_message="" if text else "这条消息没有正文也没有摘要，无法作为证据",
        created_at=timestamp,
    )


def skipped_material(project_id: str, task: Task, url: str, title: str, timestamp: int) -> Material:
    return Material(
        id=new_id(),
        project_id=project_id,
        kind=MaterialKind.WEB.value,
        status=MaterialStatus.FAILED.value,
        url=url,
        final_url="",
        title=title,
        publisher="",
        published_at=None,
        fetched_at=timestamp,
        content_text="",
        content_hash="",
        byte_size=0,
        truncated=False,
        message_version_id=None,
        task_id=task.id,
        error_code=BUDGET_EXHAUSTED,
        error_message="这一轮读取预算已用完，未读取；可重新发起资料准备",
        created_at=timestamp,
    )


def summary_json(summary: MaterialSummary, task_id: str) -> str:
    return json.dumps(
        {
            "queries": [
                {
                    "query": item.query,
                    "purpose": item.purpose,
                    "hits": item.hits,
                    "unresponsive_engines": list(item.unresponsive_engines),
                    "error_code": item.error_code,
                }
                for item in summary.queries
            ],
            "read": summary.read,
            "summary_only": summary.summary_only,
            "unavailable": summary.unavailable,
            "failed": summary.failed,
            "duplicates": summary.duplicates,
            "skipped_by_budget": summary.skipped_by_budget,
            "notes": list(summary.notes),
            "task_id": task_id,
            "finished_at": summary.finished_at,
        },
        ensure_ascii=False,
    )
