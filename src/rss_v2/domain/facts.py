"""资料整理的领域对象：关键事实、支持证据、分歧与缺口，以及资料包版本。

一次资料整理（`digest_runs`）把已读资料整理成可核对的事实清单：每条事实带引文，
引文必须能在资料正文里找到（服务端校验），来源冲突与关键缺口单独记成待办。
历史版本保留——来源更新或重跑只新增版本，不改写旧依据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FactKind(StrEnum):
    """内容类型：客观事实 / 基于事实的分析 / 推测（必须带不确定表达）。"""

    FACT = "fact"
    ANALYSIS = "analysis"
    SPECULATION = "speculation"


class FactStatus(StrEnum):
    """来源支持情况：有支持证据 / 与其他来源冲突 / 缺料待核对。"""

    SUPPORTED = "supported"
    CONFLICTING = "conflicting"
    MISSING = "missing"


class NoteKind(StrEnum):
    """待办类型：缺口（缺关键材料）、分歧（来源冲突）、一般提示。"""

    GAP = "gap"
    CONFLICT = "conflict"
    NOTE = "note"


#: 引文长度上下限：太短说不清、太长等于把整段抄回来。
MIN_QUOTE_CHARS = 10
MAX_QUOTE_CHARS = 300

#: 引文校验时的模糊匹配窗口：允许 OCR/空白/标点差异，但不允许改写。
QUOTE_FUZZ_CHARS = 30


@dataclass(frozen=True, slots=True)
class Evidence:
    """支持证据：一段引文 + 它来自哪份资料。"""

    id: str
    run_id: str
    project_id: str
    material_id: str
    quote: str
    locator: int  # 引文在资料正文中的字符偏移；-1 表示未定位
    quote_hash: str  # 引文的规范化指纹
    created_at: int


@dataclass(frozen=True, slots=True)
class Fact:
    """一条关键事实（或分析、推测）及其支持证据与状态。"""

    id: str
    run_id: str
    project_id: str
    kind: str  # 取值见 FactKind
    statement: str
    status: str  # 取值见 FactStatus
    conflict_note: str
    conflict_refs: tuple[str, ...]  # 与哪些事实冲突（同一 run 内）
    evidence: tuple[Evidence, ...] = ()
    created_at: int = 0


@dataclass(frozen=True, slots=True)
class Note:
    """缺口/分歧待办；blocking 表示未处理前不该进提纲。"""

    id: str
    run_id: str
    project_id: str
    kind: str  # 取值见 NoteKind
    text: str
    fact_id: str | None
    blocking: bool
    resolved_at: int | None
    created_at: int


@dataclass(frozen=True, slots=True)
class DigestCounts:
    """一次整理的计数：各类条数 + 被丢弃的引用（越界、引文找不到）。"""

    fact: int = 0
    analysis: int = 0
    speculation: int = 0
    evidence: int = 0
    conflict: int = 0
    gap: int = 0
    blocking: int = 0
    rejected_quotes: int = 0  # 引文在资料里找不到，被丢弃的条数
    out_of_range: int = 0  # 引用了不属于本项目资料的条数


@dataclass(frozen=True, slots=True)
class DigestRun:
    """一次资料整理的版本记录：输入指纹与各类计数。"""

    id: str
    project_id: str
    materials_hash: str
    task_id: str | None
    counts: DigestCounts = field(default_factory=DigestCounts)
    notes: tuple[str, ...] = ()
    created_at: int = 0


@dataclass(frozen=True, slots=True)
class DigestLimits:
    """资料整理的预算与上限；由配置注入，测试可以传更小的值。"""

    max_input_chars: int = 80000  # 单次调用的输入资料正文长度上限
    material_chars: int = 4000  # 单份资料放进提示词的正文长度上限
    batch_size: int = 3  # 每次调用整理几份资料；实测 6 份会被输出上限截断（含思考 token）
    max_output_tokens: int = 16384  # 单次调用的输出上限；被截断时自动对半拆批重试
    max_facts: int = 40  # 事实（含分析、推测）条数上限（全部批次合计）
    max_evidence_per_fact: int = 4  # 每条事实最多保留几条引文


@dataclass(frozen=True, slots=True)
class QuoteSupport:
    """一条引文候选：材料 ID + 引文。"""

    material_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class FactDraft:
    """服务端校验后的事实草稿：引文已定位、状态已判定。"""

    kind: str
    statement: str
    status: str
    conflict_note: str = ""
    conflict_indexes: tuple[int, ...] = ()
    supports: tuple[QuoteSupport, ...] = ()
    locators: tuple[int, ...] = ()  # 与 supports 一一对应的正文偏移
    missing_reason: str = ""  # status=missing 时的原因（例如"引文找不到"）


@dataclass(frozen=True, slots=True)
class NoteDraft:
    """待办草稿：类型、文本、是否阻塞、关联的事实序号（分歧可涉及多条）。"""

    kind: str
    text: str
    blocking: bool = False
    fact_indexes: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DigestOutcome:
    """一次整理的落库结果：版本记录 + 计数。"""

    run: DigestRun
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    notes: tuple[Note, ...] = field(default_factory=tuple)

    @property
    def has_blocking(self) -> bool:
        """是否还有未处理的阻塞缺口（未处理前不该进提纲）。"""

        return any(note.blocking and note.resolved_at is None for note in self.notes)
