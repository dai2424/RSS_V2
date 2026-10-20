"""全文的纯规则：引用核对、段落降级、来源推导与结构归一化。

模型只会给出"候选"：段落文本与类型、引用标记到事实/引文的映射、来源候选。服务端
按本次资料包逐条核对，并且对两类问题采取**不同**的处理：

- **可以丢弃的**（越界/缺引文的事实、未被正文使用的引用、重复或非法标记、空段落、
  无小标题的小节、模型自造或没用到的来源）→ 丢弃并计数，生成继续；
- **不能丢弃的**（正文用了找不到的标记、`fact` 段落没有任何有效引用、没有正文）
  → **整次失败**，不写版本行。契约原则 4 的"绝不删掉引用保留结论"就落在这一条：
  删掉引用后正文里的标记会变成悬空标记，于是整次失败，不存在"把引用抹掉让这篇
  看起来像正常的无引用文章"的中间态。

来源列表以**服务端推导**为准：正文实际引用的事实 → 引文 → 资料。模型给的候选只用来
补全展示字段与计数，自造地址在结构上进不了成稿。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from rss_v2.domain.article_text import (
    clean_links,
    mentions_uncertainty,
    normalize_kind,
    normalize_marker,
)
from rss_v2.domain.articles import (
    MARKER_PATTERN,
    MAX_HEADING_CHARS,
    MAX_LEAD_CHARS,
    MAX_PARAGRAPH_CHARS,
    MAX_SOURCES,
    MAX_TITLE_CHARS,
    MIN_SECTIONS,
    ArticleBody,
    ArticleCitation,
    ArticleCounters,
    ArticleLimits,
    ArticleParagraph,
    ArticleSection,
    ArticleSource,
    ParagraphKind,
    QuoteSnapshot,
)
from rss_v2.domain.errors import DomainError
from rss_v2.domain.facts import Fact, FactStatus
from rss_v2.domain.facts_rules import normalize_text
from rss_v2.domain.generation import ArticleDraft

#: 校验失败的错误码：模型侧记为任务失败，P6a 的人工保存直接 400。
ARTICLE_VALIDATION_FAILED = "article_validation_failed"

#: 资料行的展示信息：（标题, 发布者, 链接）；由用例层从 `Material` 提取。
MaterialInfo = tuple[str, str, str]

_MARKER = re.compile(MARKER_PATTERN)

#: 每条引用最多保留几条引文快照：引文是"这条事实有依据"的凭据，不是正文素材。
MAX_QUOTES = 4


def normalize_article(
    draft: ArticleDraft,
    *,
    facts: Sequence[Fact],
    materials: Mapping[str, MaterialInfo],
    limits: ArticleLimits,
    fallback_title: str,
) -> tuple[str, str, ArticleBody, ArticleCounters]:
    """把模型输出归一化成可落库的正文，并统计被丢弃/降级的条目。

    参数：`facts` 是本次资料包的事实（含引文）；`materials` 是本次项目已读资料的
    展示信息（id → 标题/发布者/链接）；`fallback_title` 在模型没给标题时回落到提纲标题。

    失败时抛 `DomainError(ARTICLE_VALIDATION_FAILED, ...)`：调用方（任务）不写版本行，
    把原因记进任务错误信息——用户能区分"模型没按约定作答"与"这次生成真的失败"。
    """

    counters = _Counters()
    facts_by_id = {fact.id: fact for fact in facts}
    evidence_by_id = {evidence.id: fact for fact in facts for evidence in fact.evidence}

    normalized, paragraph_markers = _normalize_sections(draft, limits, counters)
    if len(normalized) < MIN_SECTIONS:
        raise DomainError(ARTICLE_VALIDATION_FAILED, "生成结果没有正文分节")
    sections, dropped = _cap_sections(normalized, limits.max_sections)
    counters.add("dropped_sections", dropped)

    citations = _normalize_citations(draft, facts_by_id, evidence_by_id, materials, counters)
    used = {marker for group in paragraph_markers for marker in group}
    missing = sorted(used - set(citations), key=int)
    if missing:
        raise DomainError(
            ARTICLE_VALIDATION_FAILED,
            f"正文引用了没有对应引用的标记：{'、'.join(f'[^{item}]' for item in missing)}",
        )
    for marker in sorted(set(citations) - used, key=int):
        del citations[marker]
        counters.count("unused_citations")

    _require_fact_paragraphs_cited(sections, paragraph_markers, citations)
    source_list = _derive_sources(draft, sections, citations, materials, counters)

    title = normalize_text(draft.title or "")[:MAX_TITLE_CHARS].strip() or fallback_title
    lead = normalize_text(draft.lead or "")[:MAX_LEAD_CHARS].strip()
    ordered = tuple(citations[marker] for marker in sorted(citations, key=int))
    body = ArticleBody(sections=sections, citations=ordered, source_list=source_list)
    return title, lead, body, counters.snapshot()


def _normalize_sections(
    draft: ArticleDraft, limits: ArticleLimits, counters: _Counters
) -> tuple[tuple[ArticleSection, ...], list[set[str]]]:
    """逐节逐段归一化；同时收集每段的引用标记，供后续交叉核对。"""

    sections: list[ArticleSection] = []
    markers_by_paragraph: list[set[str]] = []
    for raw_section in draft.sections:
        heading = normalize_text(raw_section.heading or "")[:MAX_HEADING_CHARS].strip()
        if not heading:
            counters.count("dropped_sections")
            continue
        paragraphs: list[ArticleParagraph] = []
        for raw_paragraph in raw_section.paragraphs:
            text, rejected = clean_links(normalize_text(raw_paragraph.text or ""))
            counters.add("rejected_links", rejected)
            text = text[: min(MAX_PARAGRAPH_CHARS, limits.max_paragraph_chars)].strip()
            if not text:
                counters.count("dropped_paragraphs")
                continue
            kind, unknown = normalize_kind(raw_paragraph.kind)
            if unknown:
                counters.count("unknown_kind")
            if kind == ParagraphKind.SPECULATION.value and not mentions_uncertainty(text):
                kind = ParagraphKind.ANALYSIS.value
                counters.count("downgraded_speculation")
            paragraphs.append(ArticleParagraph(text=text, kind=kind))
            markers_by_paragraph.append(set(_MARKER.findall(text)))
        if paragraphs:
            sections.append(ArticleSection(heading=heading, paragraphs=tuple(paragraphs)))
        else:
            counters.count("dropped_sections")
    return tuple(sections), markers_by_paragraph


def _normalize_citations(
    draft: ArticleDraft,
    facts_by_id: Mapping[str, Fact],
    evidence_by_id: Mapping[str, Fact],
    materials: Mapping[str, MaterialInfo],
    counters: _Counters,
) -> dict[str, ArticleCitation]:
    """逐条核对引用：越界/缺引文的事实与非法标记丢弃并计数。"""

    citations: dict[str, ArticleCitation] = {}
    for raw in draft.citations:
        marker = normalize_marker(raw.marker)
        if marker is None:
            counters.count("invalid_markers")
            continue
        if marker in citations:
            counters.count("duplicate_markers")
            continue
        fact = facts_by_id.get((raw.fact_id or "").strip())
        if fact is None:
            counters.count("out_of_range")
            continue
        if fact.status == FactStatus.MISSING.value:
            counters.count("unsupported")
            continue
        quotes = _quotes(raw.evidence_ids, fact, evidence_by_id, materials, counters)
        if not quotes:
            # 事实本身没有可用引文（或模型的引文全被丢弃）：不能作为依据。
            counters.count("unsupported")
            continue
        citations[marker] = ArticleCitation(
            marker=marker,
            fact_id=fact.id,
            fact_kind=fact.kind,
            fact_status=fact.status,
            statement=fact.statement,
            quotes=quotes,
        )
    return citations


def _quotes(
    raw_ids: Sequence[str | None],
    fact: Fact,
    evidence_by_id: Mapping[str, Fact],
    materials: Mapping[str, MaterialInfo],
    counters: _Counters,
) -> tuple[QuoteSnapshot, ...]:
    """过滤模型给的引文 id，并快照引文原文与来源资料。

    模型没给（或给的全都越界）时回落到**该事实自己的引文**：引文本来就是服务端在
    资料整理阶段存下来的，模型漏填不等于事实没有依据。
    """

    selected: list[str] = []
    for raw_id in raw_ids:
        evidence_id = (raw_id or "").strip()
        owner = evidence_by_id.get(evidence_id)
        if owner is None:
            counters.count("out_of_range")
            continue
        if owner.id != fact.id:
            counters.count("unsupported")
            continue
        selected.append(evidence_id)
    evidence = [item for item in fact.evidence if item.id in set(selected)] if selected else []
    if not evidence:
        evidence = list(fact.evidence)
    snapshots: list[QuoteSnapshot] = []
    for item in evidence[:MAX_QUOTES]:
        info = materials.get(item.material_id)
        if info is None:
            counters.count("out_of_range")
            continue
        title, _publisher, url = info  # 发布者只进来源列表，快照里不重复保存
        snapshots.append(
            QuoteSnapshot(
                evidence_id=item.id,
                fact_id=fact.id,
                text=item.quote,
                material_id=item.material_id,
                material_title=title,
                material_url=url,
                locator=item.locator,
            )
        )
    return tuple(snapshots)


def _require_fact_paragraphs_cited(
    sections: Sequence[ArticleSection],
    markers_by_paragraph: Sequence[set[str]],
    citations: Mapping[str, ArticleCitation],
) -> None:
    """`fact` 段落必须至少带一个有效引用标记，否则整次失败（原则 3）。"""

    position = 0
    for section in sections:
        for paragraph in section.paragraphs:
            markers = markers_by_paragraph[position]
            position += 1
            if paragraph.kind != ParagraphKind.FACT.value:
                continue
            if not markers & set(citations):
                raise DomainError(
                    ARTICLE_VALIDATION_FAILED,
                    f"「{section.heading}」里有不带引用的『事实』段落："
                    "没有依据的内容应标为分析或推测",
                )


def _cap_sections(
    sections: tuple[ArticleSection, ...], limit: int
) -> tuple[tuple[ArticleSection, ...], int]:
    """按节数上限截断；返回（保留的节, 被丢弃的节数）。"""

    maximum = max(MIN_SECTIONS, limit)
    if len(sections) <= maximum:
        return sections, 0
    return sections[:maximum], len(sections) - maximum


def _derive_sources(
    draft: ArticleDraft,
    sections: Sequence[ArticleSection],
    citations: Mapping[str, ArticleCitation],
    materials: Mapping[str, MaterialInfo],
    counters: _Counters,
) -> tuple[ArticleSource, ...]:
    """按正文实际引用推导来源列表，并把模型的候选与推导结果核对。

    - 推导：正文用到的引用 → 引文快照 → 资料，按首次引用顺序去重；
    - 模型候选：匹配不到已读资料的计 `invalid_sources`，匹配到但没被正文引用的
      计 `unused_sources`；模型给了候选却漏掉正文用到的资料时计 `added_sources`
      （候选表为空是提示词允许的写法，不算漏列）。
    - 超过上限的部分丢弃并计 `dropped_sources`（截断不静默）。
    """

    used_markers = {
        marker
        for section in sections
        for paragraph in section.paragraphs
        for marker in _MARKER.findall(paragraph.text)
    }
    ordered: list[str] = []
    for marker in sorted(used_markers, key=int):
        citation = citations.get(marker)
        if citation is None:
            continue
        for quote in citation.quotes:
            if quote.material_id not in ordered:
                ordered.append(quote.material_id)
    candidates = [(raw.material_id or "").strip() for raw in draft.source_list]
    candidates = [item for item in candidates if item]
    counters.add("invalid_sources", sum(1 for item in candidates if item not in materials))
    counters.add(
        "unused_sources",
        sum(1 for item in candidates if item in materials and item not in ordered),
    )
    if candidates:
        # 提示词允许模型留空来源表（服务端会推导），所以"空表"不算漏列；
        # 只有模型给了候选却漏掉正文用到的资料时才计数。
        counters.add("added_sources", sum(1 for item in ordered if item not in candidates))
    kept = ordered[:MAX_SOURCES]
    counters.add("dropped_sources", len(ordered) - len(kept))
    sources: list[ArticleSource] = []
    for material_id in kept:
        title, publisher, url = materials[material_id]
        sources.append(
            ArticleSource(material_id=material_id, title=title, publisher=publisher, url=url)
        )
    return tuple(sources)


class _Counters:
    """可变计数器：规则里逐处自增，最后一次性冻结成 `ArticleCounters`。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def count(self, name: str) -> None:
        """某一项 +1。"""

        self.add(name, 1)

    def add(self, name: str, amount: int) -> None:
        """某一项 +amount（0 或负数忽略）。"""

        if amount > 0:
            self.values[name] = self.values.get(name, 0) + amount

    def snapshot(self) -> ArticleCounters:
        """冻结当前计数；未出现的项为 0。"""

        get = self.values.get
        return ArticleCounters(
            out_of_range=get("out_of_range", 0),
            unsupported=get("unsupported", 0),
            unused_citations=get("unused_citations", 0),
            duplicate_markers=get("duplicate_markers", 0),
            invalid_markers=get("invalid_markers", 0),
            downgraded_speculation=get("downgraded_speculation", 0),
            unknown_kind=get("unknown_kind", 0),
            rejected_links=get("rejected_links", 0),
            dropped_paragraphs=get("dropped_paragraphs", 0),
            dropped_sections=get("dropped_sections", 0),
            invalid_sources=get("invalid_sources", 0),
            unused_sources=get("unused_sources", 0),
            added_sources=get("added_sources", 0),
            dropped_sources=get("dropped_sources", 0),
        )
