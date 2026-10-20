"""全文版本的 SQLite 仓储。

版本只追加：生成与（P6a 的）人工保存都写新行，历史行不被改写。正文、引用快照与
来源列表随版本一起落库，读取时不依赖当时的资料包是否还在——这是"旧稿依据不变"的
数据基础。JSON 列的读写都在这个模块里，字段名只有一份。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import cast

from rss_v2.adapters.sqlite.connection import SQLiteDatabase
from rss_v2.adapters.sqlite.lease import ensure_lease
from rss_v2.domain import (
    ArticleBody,
    ArticleCitation,
    ArticleCounters,
    ArticleParagraph,
    ArticleSection,
    ArticleSource,
    ArticleVersion,
    QuoteSnapshot,
    TaskLease,
)

logger = logging.getLogger(__name__)

#: 计数字段：写读两侧共用一份名单，新增计数时不会只改一半。
_COUNTER_FIELDS = (
    "out_of_range",
    "unsupported",
    "unused_citations",
    "duplicate_markers",
    "invalid_markers",
    "downgraded_speculation",
    "unknown_kind",
    "rejected_links",
    "dropped_paragraphs",
    "dropped_sections",
    "invalid_sources",
    "unused_sources",
    "added_sources",
    "dropped_sources",
)


def _rows(raw: str, *, what: str) -> list[dict[str, object]]:
    """解析 JSON 数组；结构损坏时记录原因并返回空（不静默当成"没有内容"）。"""

    try:
        parsed: object = json.loads(raw or "[]")
    except ValueError:
        logger.warning("%s_unparsable", what)
        return []
    if not isinstance(parsed, list):
        logger.warning("%s_not_a_list type=%s", what, type(parsed).__name__)
        return []
    rows: list[dict[str, object]] = []
    for item in cast(list[object], parsed):
        if isinstance(item, dict):
            rows.append(cast(dict[str, object], item))
        else:
            logger.warning("%s_item_not_an_object", what)
    return rows


def _text(value: object) -> str:
    return str(value) if value is not None else ""


def _int(value: object) -> int:
    """JSON 里的数值：只认数字，其它形态按 0 处理（计数只用于提示）。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def dump_body(body: ArticleBody) -> str:
    """正文（分节段落 + 引用快照）序列化为 JSON。"""

    return json.dumps(
        {
            "sections": [
                {
                    "heading": section.heading,
                    "paragraphs": [
                        {"text": paragraph.text, "kind": paragraph.kind}
                        for paragraph in section.paragraphs
                    ],
                }
                for section in body.sections
            ],
            "citations": [
                {
                    "marker": citation.marker,
                    "fact_id": citation.fact_id,
                    "fact_kind": citation.fact_kind,
                    "fact_status": citation.fact_status,
                    "statement": citation.statement,
                    "quotes": [
                        {
                            "evidence_id": quote.evidence_id,
                            "fact_id": quote.fact_id,
                            "text": quote.text,
                            "material_id": quote.material_id,
                            "material_title": quote.material_title,
                            "material_url": quote.material_url,
                            "locator": quote.locator,
                        }
                        for quote in citation.quotes
                    ],
                }
                for citation in body.citations
            ],
        },
        ensure_ascii=False,
    )


def _body(raw: str) -> ArticleBody:
    """解析正文 JSON；分节与引用各自容错，损坏的部分记录原因后跳过。"""

    try:
        parsed: object = json.loads(raw or "{}")
    except ValueError:
        logger.warning("article_body_unparsable")
        return ArticleBody(sections=())
    if not isinstance(parsed, dict):
        logger.warning("article_body_not_an_object type=%s", type(parsed).__name__)
        return ArticleBody(sections=())
    row = cast(dict[str, object], parsed)
    sections = tuple(
        ArticleSection(
            heading=_text(item.get("heading")),
            paragraphs=tuple(
                ArticleParagraph(text=_text(p.get("text")), kind=_text(p.get("kind")) or "analysis")
                for p in _objects(item.get("paragraphs"))
            ),
        )
        for item in _objects(row.get("sections"))
    )
    citations = tuple(
        ArticleCitation(
            marker=_text(item.get("marker")),
            fact_id=_text(item.get("fact_id")),
            fact_kind=_text(item.get("fact_kind")),
            fact_status=_text(item.get("fact_status")),
            statement=_text(item.get("statement")),
            quotes=tuple(
                QuoteSnapshot(
                    evidence_id=_text(q.get("evidence_id")),
                    fact_id=_text(q.get("fact_id")),
                    text=_text(q.get("text")),
                    material_id=_text(q.get("material_id")),
                    material_title=_text(q.get("material_title")),
                    material_url=_text(q.get("material_url")),
                    locator=_int(q.get("locator")),
                )
                for q in _objects(item.get("quotes"))
            ),
        )
        for item in _objects(row.get("citations"))
    )
    return ArticleBody(sections=sections, citations=citations)


def _objects(value: object) -> list[dict[str, object]]:
    """取出 JSON 数组里的对象项；非数组时返回空。"""

    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, object], item)
        for item in cast(list[object], value)
        if isinstance(item, dict)
    ]


def dump_sources(sources: tuple[ArticleSource, ...]) -> str:
    """来源列表序列化为 JSON。"""

    return json.dumps(
        [
            {
                "material_id": source.material_id,
                "title": source.title,
                "publisher": source.publisher,
                "url": source.url,
            }
            for source in sources
        ],
        ensure_ascii=False,
    )


def _sources(raw: str) -> tuple[ArticleSource, ...]:
    return tuple(
        ArticleSource(
            material_id=_text(item.get("material_id")),
            title=_text(item.get("title")),
            publisher=_text(item.get("publisher")),
            url=_text(item.get("url")),
        )
        for item in _rows(raw, what="article_sources")
    )


def dump_counters(counters: ArticleCounters) -> str:
    """计数序列化为 JSON。"""

    return json.dumps({name: getattr(counters, name) for name in _COUNTER_FIELDS})


def _counters(raw: str) -> ArticleCounters:
    """解析计数 JSON；损坏时按零值处理（计数只用于提示，不影响正文）。"""

    try:
        parsed: object = json.loads(raw or "{}")
    except ValueError:
        logger.warning("article_counters_unparsable")
        return ArticleCounters()
    if not isinstance(parsed, dict):
        logger.warning("article_counters_not_an_object type=%s", type(parsed).__name__)
        return ArticleCounters()
    row = cast(dict[str, object], parsed)
    get = row.get
    return ArticleCounters(
        out_of_range=_int(get("out_of_range")),
        unsupported=_int(get("unsupported")),
        unused_citations=_int(get("unused_citations")),
        duplicate_markers=_int(get("duplicate_markers")),
        invalid_markers=_int(get("invalid_markers")),
        downgraded_speculation=_int(get("downgraded_speculation")),
        unknown_kind=_int(get("unknown_kind")),
        rejected_links=_int(get("rejected_links")),
        dropped_paragraphs=_int(get("dropped_paragraphs")),
        dropped_sections=_int(get("dropped_sections")),
        invalid_sources=_int(get("invalid_sources")),
        unused_sources=_int(get("unused_sources")),
        added_sources=_int(get("added_sources")),
        dropped_sources=_int(get("dropped_sources")),
    )


def _version(row: sqlite3.Row) -> ArticleVersion:
    parsed = _body(str(row["body_json"] or "{}"))
    body = ArticleBody(
        sections=parsed.sections,
        citations=parsed.citations,
        source_list=_sources(str(row["source_list_json"] or "[]")),
    )
    return ArticleVersion(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        version_number=int(row["version_number"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        title=str(row["title"]),
        lead=str(row["lead"]),
        body=body,
        body_markdown=str(row["body_markdown"]),
        outline_version_id=str(row["outline_version_id"]),
        material_snapshot_hash=str(row["material_snapshot_hash"]),
        digest_run_id=str(row["digest_run_id"]) if row["digest_run_id"] is not None else None,
        prompt_version=str(row["prompt_version"]) if row["prompt_version"] is not None else None,
        model=str(row["model"]) if row["model"] is not None else None,
        task_id=str(row["task_id"]) if row["task_id"] is not None else None,
        base_article_version_id=(
            str(row["base_article_version_id"])
            if row["base_article_version_id"] is not None
            else None
        ),
        created_at=int(row["created_at"]),
        saved_at=int(row["saved_at"]) if row["saved_at"] is not None else None,
        counters=_counters(str(row["counters_json"] or "{}")),
    )


class SQLiteArticleRepository:
    """全文版本的持久化。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def add(self, version: ArticleVersion, lease: TaskLease | None = None) -> None:
        with self.database.transaction() as connection:
            ensure_lease(connection, lease)
            connection.execute(
                "INSERT INTO article_versions(id,project_id,outline_version_id,version_number,"
                " kind,status,title,lead,body_json,body_markdown,source_list_json,counters_json,"
                " material_snapshot_hash,digest_run_id,prompt_version,model,task_id,"
                " base_article_version_id,recheck_fact_ids_json,created_at,saved_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version.id,
                    version.project_id,
                    version.outline_version_id,
                    version.version_number,
                    version.kind,
                    version.status,
                    version.title,
                    version.lead,
                    dump_body(version.body),
                    version.body_markdown,
                    dump_sources(version.body.source_list),
                    dump_counters(version.counters),
                    version.material_snapshot_hash,
                    version.digest_run_id,
                    version.prompt_version,
                    version.model,
                    version.task_id,
                    version.base_article_version_id,
                    "[]",
                    version.created_at,
                    version.saved_at,
                ),
            )

    def get(self, version_id: str) -> ArticleVersion | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM article_versions WHERE id=?", (version_id,)
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def versions(self, project_id: str, limit: int = 20) -> list[ArticleVersion]:
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM article_versions WHERE project_id=?"
                " ORDER BY version_number DESC, rowid DESC LIMIT ?",
                (project_id, max(1, limit)),
            ).fetchall()
            return [_version(row) for row in rows]
        finally:
            connection.close()

    def latest_number(self, project_id: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(version_number),0) AS n FROM article_versions"
                " WHERE project_id=?",
                (project_id,),
            ).fetchone()
            return int(row["n"])
        finally:
            connection.close()

    def saved(self, project_id: str) -> ArticleVersion | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT * FROM article_versions WHERE project_id=? AND status='saved'"
                " ORDER BY version_number DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            return _version(row) if row else None
        finally:
            connection.close()

    def count(self, project_id: str) -> int:
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM article_versions WHERE project_id=?", (project_id,)
            ).fetchone()
            return int(row["n"])
        finally:
            connection.close()

    def delete(self, version_id: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM article_versions WHERE id=?", (version_id,))
            return cursor.rowcount if cursor.rowcount > 0 else 0
