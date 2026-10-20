"""全文预览的组装：受控 HTML、引用证据与待核对项。

预览一次返回界面需要的全部内容：正文 HTML、每个引用标记对应的事实陈述与引文原文、
未处理的缺口/分歧。前端点标记就地展开证据时不必再发请求，也就不会因为二次请求失败
而让读者卡在半截。

引用与引文都来自**版本里的快照**，不重新查询资料包：旧稿的依据不随资料重跑而改变。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rss_v2.domain import ArticleVersion, Note
from rss_v2.domain.article_markdown import render_article_html


@dataclass(frozen=True, slots=True)
class QuotePreview:
    """一条引文：原文、来源资料与链接。"""

    text: str
    locator: int
    material_id: str
    material_title: str
    material_url: str


@dataclass(frozen=True, slots=True)
class CitationPreview:
    """一个引用标记对应的证据；`status` 为生成时的核对结论。"""

    marker: str
    fact_id: str
    fact_kind: str
    fact_status: str
    statement: str
    quotes: tuple[QuotePreview, ...]


@dataclass(frozen=True, slots=True)
class NotePreview:
    """一条未处理的缺口/分歧；`blocking` 表示影响主线。"""

    kind: str
    text: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class ArticlePreview:
    """预览载荷：正文 HTML + 证据 + 待核对项 + 生成时被丢弃的条目。"""

    version: ArticleVersion
    html: str
    citations: tuple[CitationPreview, ...]
    pending_notes: tuple[NotePreview, ...]
    blocking_count: int

    @property
    def has_blocking(self) -> bool:
        """是否还有未处理的阻塞缺口（只标记，不拦生成）。"""

        return self.blocking_count > 0

    @property
    def source_count(self) -> int:
        """文末来源数量；为 1 时界面要提示"单一来源"。"""

        return self.version.source_count


def build_preview(
    version: ArticleVersion, notes: Sequence[Note], blocking_count: int
) -> ArticlePreview:
    """按版本快照组装预览；`notes` 是该版本绑定资料包里仍未处理的缺口/分歧。"""

    citations = tuple(
        CitationPreview(
            marker=citation.marker,
            fact_id=citation.fact_id,
            fact_kind=citation.fact_kind,
            fact_status=citation.fact_status,
            statement=citation.statement,
            quotes=tuple(
                QuotePreview(
                    text=quote.text,
                    locator=quote.locator,
                    material_id=quote.material_id,
                    material_title=quote.material_title,
                    material_url=quote.material_url,
                )
                for quote in citation.quotes
            ),
        )
        for citation in version.body.citations
    )
    pending = tuple(
        NotePreview(kind=note.kind, text=note.text, blocking=note.blocking)
        for note in notes
        if note.resolved_at is None
    )
    return ArticlePreview(
        version=version,
        html=render_article_html(version.title, version.lead, version.body),
        citations=citations,
        pending_notes=pending,
        blocking_count=blocking_count,
    )
