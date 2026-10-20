"""提纲的纯规则：越界引用过滤、结构截断与有效性判定。

模型（或人工保存的表单）只能给出事实 id 与引文 id，服务端负责核对"这些 id 是不是
本项目的、有没有可用引文"，并把越界引用、缺依据的小节逐条统计出来——不允许只凭
"id 看起来合法"就把一段内容当成有依据。放在 domain 便于单测，也让用例层只关心
调用与落库。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from rss_v2.domain.errors import DomainError
from rss_v2.domain.facts import Fact, FactKind, FactStatus, Note, NoteKind
from rss_v2.domain.facts_rules import normalize_text
from rss_v2.domain.generation import OutlineDraft
from rss_v2.domain.outlines import (
    MAX_ANGLE_CHARS,
    MAX_HEADING_CHARS,
    MAX_POINT_CHARS,
    MAX_TITLE_CHARS,
    MIN_SECTIONS,
    OutlineCounters,
    OutlineSection,
    OutlineVersion,
)

#: 校验失败的错误码：模型输出走任务失败，人工保存直接 400。
OUTLINE_VALIDATION_FAILED = "outline_validation_failed"

#: 事实类型在提示词里的中文标注；模型据此区分客观事实与推测。
_KIND_LABELS = {
    FactKind.FACT.value: "客观事实",
    FactKind.ANALYSIS.value: "分析",
    FactKind.SPECULATION.value: "推测",
}

#: 提示词里每条事实最多列几条引文：引文只是"这条事实有依据"的凭据，
#: 全列出来会把输入撑满，反而挤掉别的事实。
MAX_QUOTES_IN_PROMPT = 2

#: 提示词里最多列几条未处理待办。
MAX_NOTES_IN_PROMPT = 20


def clean_line(text: str, limit: int) -> str:
    """单行文本归一化：折叠空白并截断到上限。"""

    return normalize_text(text)[:limit]


def clean_points(items: Iterable[str], limit: int) -> tuple[tuple[str, ...], int]:
    """要点列表归一化：去空、按顺序去重、截断到上限，返回（保留项, 被丢弃条数）。"""

    kept: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = clean_line(item, MAX_POINT_CHARS)
        if not text or text in seen:
            continue
        seen.add(text)
        kept.append(text)
    dropped = max(0, len(kept) - limit)
    return tuple(kept[:limit]), dropped


def normalize_outline(
    draft: OutlineDraft,
    *,
    fact_status: Mapping[str, str],
    evidence_ids: Mapping[str, frozenset[str]],
    max_sections: int,
    max_points: int,
) -> tuple[tuple[OutlineSection, ...], OutlineCounters]:
    """把模型/表单输出归一化成可落库的分节，并统计被丢弃的引用。

    - `fact_status`：本次可用事实的 id → 状态（只有本次资料包的事实在里面）；
    - `evidence_ids`：事实 id → 它自己的引文 id；
    - 事实 id 越界、引文 id 越界都逐条计数；指向 `missing` 事实的引用单独计数
      （那条事实自己还没有可用引文，拿它当依据等于没有依据）；
    - 没有小标题的小节、超出节数上限的小节丢弃并计数；
    - 保留下来但一条事实依据都没有的小节计入 `without_facts`，界面要显著提示
      "这一节没有依据"——它可能是模型在凑结构。

    归一化后少于 `MIN_SECTIONS` 节时判为无效输出：报错而不是落一个残骸版本，
    让"模型没按约定作答"与"这个选题写不出提纲"在界面上能区分开。
    """

    counters = {"out_of_range": 0, "unsupported": 0, "dropped_sections": 0, "dropped_points": 0}
    owner = {evidence_id: fact_id for fact_id, ids in evidence_ids.items() for evidence_id in ids}
    sections: list[OutlineSection] = []
    for raw in draft.sections:
        heading = clean_line(raw.heading, MAX_HEADING_CHARS)
        if not heading:
            counters["dropped_sections"] += 1
            continue
        points, dropped_points = clean_points(raw.talking_points, max_points)
        impact, dropped_impact = clean_points(raw.impact_notes, max_points)
        counters["dropped_points"] += dropped_points + dropped_impact
        facts, fact_counters = _valid_facts(raw.fact_ids, fact_status)
        counters["out_of_range"] += fact_counters[0]
        counters["unsupported"] += fact_counters[1]
        evidence, evidence_counters = _valid_evidence(raw.evidence_ids, owner, set(facts))
        counters["out_of_range"] += evidence_counters[0]
        counters["unsupported"] += evidence_counters[1]
        sections.append(
            OutlineSection(
                heading=heading,
                talking_points=points,
                fact_ids=facts,
                evidence_ids=evidence,
                impact_notes=impact,
            )
        )
    if len(sections) > max_sections:
        counters["dropped_sections"] += len(sections) - max_sections
        sections = sections[:max_sections]
    if len(sections) < MIN_SECTIONS:
        raise DomainError(
            OUTLINE_VALIDATION_FAILED,
            f"提纲不足 {MIN_SECTIONS} 节，无法作为写作提纲",
        )
    without_facts = sum(1 for section in sections if not section.fact_ids)
    return (
        tuple(sections),
        OutlineCounters(
            out_of_range=counters["out_of_range"],
            unsupported=counters["unsupported"],
            dropped_sections=counters["dropped_sections"],
            dropped_points=counters["dropped_points"],
            without_facts=without_facts,
        ),
    )


def normalize_title(draft: OutlineDraft) -> tuple[str, str]:
    """标题与切入点：归一化并截断；标题为空时留给调用方回落。"""

    return clean_line(draft.title, MAX_TITLE_CHARS), clean_line(draft.angle, MAX_ANGLE_CHARS)


def fact_index(facts: Sequence[Fact]) -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """一次资料包的事实索引：事实 id → 状态，事实 id → 它自己的引文 id 集合。

    校验提纲引用时用同一份索引：能引用的事实只限这次资料包里的，引文只限这些事实
    自己的。
    """

    status: dict[str, str] = {}
    evidence: dict[str, frozenset[str]] = {}
    for fact in facts:
        status[fact.id] = fact.status
        evidence[fact.id] = frozenset(item.id for item in fact.evidence)
    return status, evidence


def facts_text(facts: Sequence[Fact], *, limit: int, fact_chars: int) -> str:
    """渲染提示词里的事实块：每条含 id、类型、陈述与（最多两条）带 id 的引文。

    事实 id 与引文 id 都必须出现在提示词里，否则模型没有可引用的依据（引文 id 不列出时，
    模型只能把 `evidence_ids` 留空或编造，编造的会被服务端丢弃）；`missing` 的事实照样
    列出来，但明确标注"没有可用引文"——把它藏起来会让模型以为资料比实际更完整。
    """

    blocks: list[str] = []
    for fact in facts:
        label = _KIND_LABELS.get(fact.kind, fact.kind)
        head = f"[{fact.id}] {fact.statement[:fact_chars]}（{label}）"
        if fact.status == FactStatus.MISSING.value:
            blocks.append(f"{head}\n状态：没有可用引文，不要作为依据")
            continue
        if fact.status == FactStatus.CONFLICTING.value:
            head = f"{head}\n状态：来源之间存在分歧"
        quotes = [
            f"引文 [{item.id}]：{item.quote}" for item in fact.evidence[:MAX_QUOTES_IN_PROMPT]
        ]
        blocks.append("\n".join([head, *quotes]))
    return "\n\n".join(blocks)[:limit]


def outline_text(outline: OutlineVersion, *, limit: int) -> str:
    """把已确认的提纲渲染成提示词里的提纲块：标题、切入点、逐节要点与影响。

    全文生成按它组织正文；要点里的判断就是用户确认过的结构，模型不该自己另起一套。
    """

    lines = [f"标题：{outline.title}"]
    if outline.angle:
        lines.append(f"切入点：{outline.angle}")
    for index, section in enumerate(outline.sections, 1):
        lines.append(f"第 {index} 节 {section.heading}")
        lines.extend(f"- {point}" for point in section.talking_points)
        lines.extend(f"- 影响：{note}" for note in section.impact_notes)
    return "\n".join(lines)[:limit]


def notes_text(notes: Sequence[Note], *, limit: int) -> str:
    """渲染提示词里未处理的缺口与分歧块；已处理的待办不再列出。"""

    blocks: list[str] = []
    for note in notes:
        if note.resolved_at is not None:
            continue
        if note.kind == NoteKind.CONFLICT.value:
            blocks.append(f"分歧：{note.text}")
        elif note.blocking:
            blocks.append(f"阻塞缺口：{note.text}")
        else:
            blocks.append(f"提示：{note.text}")
        if len(blocks) >= MAX_NOTES_IN_PROMPT:
            break
    return "\n".join(blocks)[:limit] or "（没有未处理的缺口与分歧）"


def _valid_facts(
    raw_ids: Iterable[str], fact_status: Mapping[str, str]
) -> tuple[tuple[str, ...], tuple[int, int]]:
    """过滤事实 id，返回（保留的 id, （越界数, 缺引文数））。"""

    kept: list[str] = []
    seen: set[str] = set()
    out_of_range = 0
    unsupported = 0
    for raw_id in raw_ids:
        fact_id = raw_id.strip()
        if not fact_id or fact_id in seen:
            continue
        seen.add(fact_id)
        status = fact_status.get(fact_id)
        if status is None:
            out_of_range += 1
            continue
        if status == FactStatus.MISSING.value:
            unsupported += 1
            continue
        kept.append(fact_id)
    return tuple(kept), (out_of_range, unsupported)


def _valid_evidence(
    raw_ids: Iterable[str], owner: Mapping[str, str], kept_facts: set[str]
) -> tuple[tuple[str, ...], tuple[int, int]]:
    """过滤引文 id：只保留属于保留事实的引文，返回（保留的 id, （越界数, 缺依据数））。"""

    kept: list[str] = []
    seen: set[str] = set()
    out_of_range = 0
    unsupported = 0
    for raw_id in raw_ids:
        evidence_id = raw_id.strip()
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        fact_id = owner.get(evidence_id)
        if fact_id is None:
            out_of_range += 1
            continue
        if fact_id not in kept_facts:
            # 引文本身存在，但它所属的事实被丢弃了（越界或没有可用引文）。
            unsupported += 1
            continue
        kept.append(evidence_id)
    return tuple(kept), (out_of_range, unsupported)
