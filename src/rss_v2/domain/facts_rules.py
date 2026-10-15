"""资料整理的纯规则：引文定位、事实归一化、冲突与缺口判定。

模型只能给"材料 ID + 原样引文"，服务端负责把引文在资料正文里定位（内容支持关系的
第一层校验），并把越界引用、找不到的引文、缺证据的事实逐条统计出来——不允许只凭
"ID 合法"就当成有依据。放在 domain 便于单测，也让用例层只关心调用与落库。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import replace

from rss_v2.domain.facts import (
    MAX_QUOTE_CHARS,
    MIN_QUOTE_CHARS,
    FactDraft,
    FactKind,
    FactStatus,
    NoteDraft,
    NoteKind,
    QuoteSupport,
)
from rss_v2.domain.generation import FactSchema, MaterialDigest

#: 归一化时保留的字符：中英文、数字与常见符号；其余（空白、标点）压成单个空格。
_PUNCT = re.compile(r"[\s\u3000]+")
_QUOTE_SOFT = re.compile(r"[\s\u3000「」“”\"'’‘.,，。；;：:!！?？()（）\[\]【】—\-…]+")


def normalize_text(text: str) -> str:
    """空白压缩：用于把"内容里是不是真有这段"变成逐字比较。"""

    return _PUNCT.sub(" ", text).strip()


def soft_normalize(text: str) -> str:
    """更宽松的归一化：连标点一起压掉。

    网页正文里标点与引号常有差异（半角/全角、中文书名号），若按逐字比较会把正确引文
    判成找不到；但改写句子（增删实词）仍然过不了这一关。
    """

    return _QUOTE_SOFT.sub("", text)


def quote_hash(quote: str) -> str:
    return hashlib.sha256(soft_normalize(quote).encode("utf-8")).hexdigest()


def locate_quote(text: str, quote: str) -> int:
    """在资料正文里定位引文，返回字符偏移；找不到返回 -1。"""

    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        return -1
    direct = text.find(normalized_quote)
    if direct >= 0:
        return direct
    # 退化到宽松比较：按标点切段后逐段查找，命中则返回首段位置。
    parts = [part for part in _QUOTE_SOFT.split(normalized_quote) if part]
    if not parts:
        return -1
    return text.find(parts[0])


def clean_quote(quote: str) -> str:
    """清洗引文：折叠空白、截断到上限；太短（少于下限）返回空串。"""

    cleaned = normalize_text(quote)
    if len(cleaned) < MIN_QUOTE_CHARS:
        return ""
    return cleaned[:MAX_QUOTE_CHARS]


def normalize_kind(raw: str) -> str:
    """内容类型归一化：只认 fact/analysis/speculation，中文写法归一到对应值。"""

    value = raw.strip().lower()
    if value in {"分析", "analysis"}:
        return FactKind.ANALYSIS.value
    if value in {"推测", "speculation", "guess"}:
        return FactKind.SPECULATION.value
    return FactKind.FACT.value


def normalize_digest(
    digest: MaterialDigest,
    materials: dict[str, str],
    *,
    max_facts: int,
    max_supports: int,
) -> tuple[list[FactDraft], list[NoteDraft], dict[str, int]]:
    """把模型输出归一化成事实草稿与待办草稿，并统计被丢弃的引用。

    - `materials`：材料 ID → 资料正文（只有本项目已读资料才在表里）；
    - 引文必须在对应资料里能定位，否则丢弃该条引文（若一条事实的引文全被丢弃，
      该事实记为 `missing` 并在缺口里说明，而不是当作"有依据"）；
    - 分歧涉及的序号越界时丢弃该条分歧并计数。
    """

    out_of_range = 0
    rejected_quotes = 0
    facts: list[FactDraft] = []
    for raw in digest.facts[: max(1, max_facts)]:
        statement = normalize_text(raw.statement)
        if not statement:
            continue
        supports: list[QuoteSupport] = []
        locators: list[int] = []
        for item in raw.supports:
            material_id = item.material_id.strip()
            text = materials.get(material_id)
            if text is None:
                out_of_range += 1
                continue
            quote = clean_quote(item.quote)
            if not quote:
                rejected_quotes += 1
                continue
            locator = locate_quote(text, quote)
            if locator < 0:
                rejected_quotes += 1
                continue
            supports.append(QuoteSupport(material_id=material_id, quote=quote))
            locators.append(locator)
            if len(supports) >= max(1, max_supports):
                break
        kind = normalize_kind(raw.kind)
        status = FactStatus.SUPPORTED.value if supports else FactStatus.MISSING.value
        facts.append(
            FactDraft(
                kind=kind,
                statement=statement,
                status=status,
                conflict_indexes=tuple(_conflict_indexes(raw)),
                supports=tuple(supports),
                locators=tuple(locators),
                missing_reason="" if supports else "模型没有给出能在资料里定位的引文",
            )
        )

    notes: list[NoteDraft] = []
    for conflict in digest.conflicts:
        text = normalize_text(conflict.text)
        if not text:
            continue
        valid = tuple(index for index in conflict.statement_indexes if 0 <= int(index) < len(facts))
        if conflict.statement_indexes and not valid:
            out_of_range += 1
        notes.append(NoteDraft(kind=NoteKind.CONFLICT.value, text=text, fact_indexes=valid))
    for gap in digest.gaps:
        text = normalize_text(gap.text)
        if not text:
            continue
        notes.append(NoteDraft(kind=NoteKind.GAP.value, text=text, blocking=bool(gap.blocking)))
    # 缺证据的事实本身也是缺口：明确提示"这条关键事实还找不到依据"。
    for index, fact in enumerate(facts):
        if fact.status == FactStatus.MISSING.value:
            notes.append(
                NoteDraft(
                    kind=NoteKind.GAP.value,
                    text=f"关键事实缺少可用引文：{fact.statement[:60]}",
                    blocking=True,
                    fact_indexes=(index,),
                )
            )
    return facts, notes, {"out_of_range": out_of_range, "rejected_quotes": rejected_quotes}


def _conflict_indexes(raw: FactSchema) -> Sequence[int]:
    """事实自身的分歧序号由 conflicts 统一给出，这里只返回空（保留扩展位）。"""

    return ()


def mark_conflicts(
    facts: Sequence[FactDraft], notes: Sequence[NoteDraft]
) -> tuple[list[FactDraft], list[NoteDraft]]:
    """把分歧落到具体事实：涉及的事实标成 conflicting，并写明冲突说明。"""

    updated = list(facts)
    for note in notes:
        if note.kind != NoteKind.CONFLICT.value or not note.fact_indexes:
            continue
        for index in note.fact_indexes:
            if not 0 <= index < len(updated):
                continue
            current = updated[index]
            updated[index] = replace(
                current,
                status=FactStatus.CONFLICTING.value,
                conflict_note=note.text,
            )
    return updated, list(notes)


def materials_fingerprint(materials: Sequence[tuple[str, str]]) -> str:
    """资料包输入指纹：(材料 ID, 内容指纹) 有序列表的哈希。"""

    digest = hashlib.sha256()
    for material_id, content_hash in materials:
        digest.update(f"{material_id}:{content_hash}\n".encode())
    return digest.hexdigest()[:24]


def materials_text(entries: Sequence[tuple[str, str, str, str]], limit: int) -> str:
    """渲染提示词里的资料块：每条含材料 ID、标题、来源与正文片段。"""

    blocks: list[str] = []
    for material_id, title, source, text in entries:
        blocks.append(f"[材料 {material_id}] {title or '（无标题）'}（{source}）\n正文：{text}")
    joined = "\n\n".join(blocks)
    return joined[:limit]
