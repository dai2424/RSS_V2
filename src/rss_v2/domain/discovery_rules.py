"""选题发现的纯规则：切批、输入指纹、提示词文本、候选归一化、跨批合并与排序。

这些函数只依赖领域对象与结构化输出 schema，不碰数据库、网络和提示词库，因此可以
单独验证（例如"同一分钟入的多条消息不能被切成碎片批"），也让用例层只负责调用时机
与结果写回。规则放在 domain 而不是用例层，是因为它描述的就是"什么算同一件事"的
领域口径。

配套口径见 docs/决策记录/2026-10-15-事件选题发现与聚合.md 第 3 节。
"""

from __future__ import annotations

import datetime
import hashlib
from collections.abc import Sequence
from dataclasses import replace

from rss_v2.domain.discovery import (
    CandidateDraft,
    DiscoveryInputVersion,
    DiscoveryStats,
    ReasonKind,
    RecommendationReason,
)
from rss_v2.domain.generation import DiscoveryDigest, DiscoveryReasonSchema

#: 候选标题/概要入库前的截断长度：标题还要塞进线索名（80）。
TITLE_LIMIT = 120
SUMMARY_LIMIT = 600
LEAD_TITLE_LIMIT = 80

#: 每条候选最多保留几条推荐理由。
MAX_REASONS = 3

#: 切批时允许的最短批长占上限的比例：避免真实数据里的零间隔把批次切碎。
MIN_BATCH_RATIO = 0.6

#: 输入文本里每条消息的摘要长度：先按这个长度渲染，超出批次上限再整体压缩。
SUMMARY_IN_PROMPT = 300
SUMMARY_IN_PROMPT_TIGHT = 120


def batch_count(messages: int, size: int) -> int:
    """预估批次数：用于日志（真实切分会按时间间隔前后浮动一两条）。"""

    return (messages + max(1, size) - 1) // max(1, size)


def chunk_inputs(
    ordered: Sequence[DiscoveryInputVersion], size: int
) -> list[list[DiscoveryInputVersion]]:
    """按条数上限切分输入，切点优先落在本批内最大的时间间隔之后，并重叠一条边界消息。

    两件事都是为了"同一事件不被拆到两批"：

    - 事件在时间上连续、事件之间通常有明显间隔，因此切在最大间隔处比"每满 N 条就切"
      更少切断事件；
    - 相邻批重叠最后一条消息，跨越切点的事件（前批尾部 + 后批头部）会在两批里都出现，
      于是共享成员，能被 merge_candidates 并回一个候选——否则跨批事件只能拆成两个候选。
    """

    if size <= 1:
        return [[item] for item in ordered]
    # 批长下限：只在 [min_len, size] 区间里找切点。真实数据里同一分钟可能入多条消息
    # （间隔为 0），若允许在任意位置切，会出现"1 条一批"的碎片批，调用次数翻数倍。
    min_len = max(1, int(size * MIN_BATCH_RATIO))
    chunks: list[list[DiscoveryInputVersion]] = []
    index = 0
    total = len(ordered)
    while index < total:
        if total - index <= size:
            chunks.append(list(ordered[index:]))
            break
        cut = index + size
        best_gap = 0
        # 候选切点：第 position 条之前（position ∈ [index+min_len, index+size]）；
        # 同分取最后一个（>=），让切点尽量靠后、批次尽量装满。
        for position in range(index + min_len, index + size + 1):
            gap = ordered[position].event_time - ordered[position - 1].event_time
            if gap > 0 and gap >= best_gap:
                best_gap, cut = gap, position
        chunks.append(list(ordered[index:cut]))
        # 重叠一条：cut-1 既是这一批的尾巴，也是下一批的开头；至少前进一条避免死循环。
        index = max(index + 1, cut - 1)
    return chunks


def input_fingerprint(inputs: Sequence[DiscoveryInputVersion]) -> str:
    """输入快照指纹：窗口内 (消息, 版本) 有序列表的哈希。"""

    digest = hashlib.sha256()
    for item in inputs:
        digest.update(f"{item.message_id}:{item.version_id}\n".encode())
    return digest.hexdigest()[:32]


def scope_hash(category_id: str | None, source_id: str | None) -> str:
    """范围哈希：只含筛选条件（不含时间）。

    时间不进幂等键：重复点击时窗口会随请求时刻滑动，但只要有窗口没有新消息，
    输入指纹就相同，应该复用同一个批次而不是重复付费。批次里仍记录真实窗口，
    界面按批次显示"实际生效范围"。
    """

    digest = hashlib.sha256()
    digest.update(f"{category_id or ''}|{source_id or ''}".encode())
    return digest.hexdigest()[:16]


def messages_text(
    chunk: Sequence[DiscoveryInputVersion], summary_limit: int, char_limit: int
) -> str:
    """把一批消息渲染成提示词里的行。

    每行：消息 ID、时间（UTC 秒与北京时间）、来源、标题、摘要、关键词。
    """

    lines: list[str] = []
    for item in chunk:
        parts = [
            f"[{item.message_id}]",
            f"{item.event_time}秒（{_beijing(item.event_time)}）",
            item.source_name,
        ]
        parts.append(item.title.replace("\n", " ").strip() or "（无标题）")
        summary = item.summary.replace("\n", " ").strip()
        if summary:
            parts.append(summary[:summary_limit])
        if item.keywords:
            parts.append("关键词：" + "、".join(item.keywords[:12]))
        lines.append(" | ".join(parts))
    return "\n".join(lines)[:char_limit]


_BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def _beijing(timestamp: int) -> str:
    """UTC 秒 → 北京时间字符串（提示词里给人看的时间）。"""

    return datetime.datetime.fromtimestamp(timestamp, _BEIJING).strftime("%m-%d %H:%M")


def normalize_candidates(
    digest: DiscoveryDigest,
    chunk: Sequence[DiscoveryInputVersion],
    max_members: int,
) -> tuple[list[CandidateDraft], DiscoveryStats]:
    """把一批模型输出转成候选草稿：越界/批内重复/超限逐条计数，不静默丢弃。

    这里只处理**批内**冲突；跨批的重叠消息留给 merge_candidates 合并、resolve_conflicts 消解。
    """

    index = {item.message_id: item for item in chunk}
    seen: set[str] = set()
    drafts: list[CandidateDraft] = []
    out_of_range = 0
    duplicates = 0
    dropped_members = 0
    dropped_candidates = 0
    for raw in digest.candidates:
        picked: list[DiscoveryInputVersion] = []
        for message_id in raw.member_message_ids:
            item = index.get(message_id)
            if item is None:
                out_of_range += 1
                continue
            if message_id in seen:
                duplicates += 1
                continue
            seen.add(message_id)
            picked.append(item)
        if not picked:
            continue
        reasons = _reasons(raw.reasons)
        title = raw.title.strip()[:TITLE_LIMIT]
        if not title or not reasons:
            # 标题或理由为空说明这条候选无法解释给用户看，丢弃并计入"未纳入候选"。
            dropped_candidates += 1
            continue
        candidate_dropped = 0
        if len(picked) > max_members:
            candidate_dropped = len(picked) - max_members
            dropped_members += candidate_dropped
            picked = picked[:max_members]
        drafts.append(
            CandidateDraft(
                title=title,
                summary=(raw.summary.strip() or title)[:SUMMARY_LIMIT],
                event_time=_event_time(raw.event_time, picked),
                score=max(1, min(5, raw.score)),
                reasons=reasons,
                version_ids=tuple(item.version_id for item in picked),
                dropped_members=candidate_dropped,
            )
        )
    return drafts, DiscoveryStats(
        duplicate_members=duplicates,
        out_of_range=out_of_range,
        dropped_members=dropped_members,
        dropped_candidates=dropped_candidates,
    )


def _reasons(
    raw_reasons: Sequence[DiscoveryReasonSchema],
) -> tuple[RecommendationReason, ...]:
    """归一化推荐理由：只认 interesting/important，丢弃空文本，最多保留三条。"""

    reasons: list[RecommendationReason] = []
    for item in raw_reasons:
        kind = str(item.kind).strip().lower()
        text = str(item.text).strip()
        if kind in {"重要", "importance"}:
            kind = ReasonKind.IMPORTANT.value
        if kind in {"有趣", "interesting"}:
            kind = ReasonKind.INTERESTING.value
        if kind not in {ReasonKind.INTERESTING.value, ReasonKind.IMPORTANT.value} or not text:
            continue
        reasons.append(RecommendationReason(kind=kind, text=text[:200]))
        if len(reasons) >= MAX_REASONS:
            break
    return tuple(reasons)


def _event_time(raw: int | None, members: Sequence[DiscoveryInputVersion]) -> int | None:
    """事件时间：模型给出且合理时用它，否则回落到成员里最早的时间。"""

    if isinstance(raw, int) and raw > 0:
        return raw
    return min((item.event_time for item in members), default=None)


def merge_candidates(drafts: Sequence[CandidateDraft]) -> list[CandidateDraft]:
    """跨批合并：共享任一成员版本即并集（确定、零额外调用）。"""

    if not drafts:
        return []
    parent = list(range(len(drafts)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owner: dict[str, int] = {}
    for index, draft in enumerate(drafts):
        for version_id in draft.version_ids:
            first = owner.setdefault(version_id, index)
            if first != index:
                union(first, index)
    groups: dict[int, list[CandidateDraft]] = {}
    for index, draft in enumerate(drafts):
        groups.setdefault(find(index), []).append(draft)
    return [_merge_group(group) for group in groups.values()]


def resolve_conflicts(
    drafts: Sequence[CandidateDraft],
) -> tuple[list[CandidateDraft], DiscoveryStats]:
    """一条消息只能留在一个候选里：按推荐度排序后先到先得，后来者计数。

    合并之后仍可能有两个候选争抢同一条消息（例如重叠消息把两个不同事件桥接起来），
    此时保留更值得写的那个候选，另一个丢掉这条成员；成员被清空的候选整条丢弃。
    """

    duplicates = 0
    kept: list[CandidateDraft] = []
    seen: set[str] = set()
    for draft in sorted(drafts, key=rank_key):
        members = tuple(version_id for version_id in draft.version_ids if version_id not in seen)
        duplicates += len(draft.version_ids) - len(members)
        if not members:
            continue
        seen.update(members)
        kept.append(
            draft if len(members) == len(draft.version_ids) else replace(draft, version_ids=members)
        )
    return kept, DiscoveryStats(duplicate_members=duplicates)


def rank_key(draft: CandidateDraft) -> tuple[int, int, str]:
    """排序键：推荐度降序 → 事件时间早的在前 → 标题；跨批比较时稳定可复现。"""

    return (-draft.score, draft.event_time if draft.event_time is not None else 2**62, draft.title)


def _merge_group(group: Sequence[CandidateDraft]) -> CandidateDraft:
    if len(group) == 1:
        return group[0]
    best = max(group, key=lambda item: (item.score, -len(item.title)))
    members: dict[str, None] = {}
    for draft in group:
        for version_id in draft.version_ids:
            members.setdefault(version_id, None)
    reasons: dict[tuple[str, str], RecommendationReason] = {}
    for draft in group:
        for reason in draft.reasons:
            reasons.setdefault((reason.kind, reason.text), reason)
    times = [draft.event_time for draft in group if draft.event_time is not None]
    return CandidateDraft(
        title=best.title,
        summary=best.summary,
        event_time=min(times) if times else None,
        score=max(draft.score for draft in group),
        reasons=tuple(list(reasons.values())[:MAX_REASONS]),
        version_ids=tuple(members.keys()),
        dropped_members=sum(draft.dropped_members for draft in group),
    )


def rank_candidates(
    drafts: Sequence[CandidateDraft], max_candidates: int
) -> tuple[list[CandidateDraft], int]:
    """排序并截断：推荐度降序 → 事件时间早的在前 → 标题；超出上限的部分计数。"""

    ordered = sorted(drafts, key=rank_key)
    if len(ordered) <= max_candidates:
        return ordered, 0
    return ordered[:max_candidates], len(ordered) - max_candidates
