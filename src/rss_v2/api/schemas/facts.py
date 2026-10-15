"""资料整理的请求与响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class EvidenceResponse(BaseModel):
    """一条支持证据：引文 + 它来自哪份资料。"""

    id: str
    material_id: str
    quote: str
    locator: int  # 引文在资料正文中的字符偏移；-1 表示未定位
    quote_hash: str


class FactResponse(BaseModel):
    """一条事实/分析/推测，带状态与支持证据。"""

    id: str
    kind: str  # fact / analysis / speculation
    statement: str
    status: str  # supported / conflicting / missing
    conflict_note: str
    evidence: list[EvidenceResponse]


class NoteResponse(BaseModel):
    """缺口/分歧待办；blocking 表示未处理前不该进提纲。"""

    id: str
    kind: str  # gap / conflict / note
    text: str
    fact_id: str | None
    blocking: bool
    resolved_at: int | None


class DigestRunResponse(BaseModel):
    """资料包版本：输入指纹与各类计数。"""

    id: str
    materials_hash: str
    task_id: str | None
    counts: dict[str, int]
    notes: list[str]
    created_at: int


class DigestResponse(BaseModel):
    """一次整理的完整视图。"""

    run: DigestRunResponse
    facts: list[FactResponse]
    notes: list[NoteResponse]
    has_blocking: bool
    task_status: str | None


class DigestHistoryResponse(BaseModel):
    """历史整理版本列表（新到旧）。"""

    items: list[DigestRunResponse]


class NoteResolutionResponse(BaseModel):
    """待办处理结果。"""

    note: NoteResponse
