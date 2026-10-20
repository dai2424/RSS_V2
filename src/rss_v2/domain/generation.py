"""阶段 4 生成任务的结构化输出 schema（Pydantic）。

放在 domain 而不是适配器里：服务层要按 schema 校验模型输出并据此过滤越界引用，
而服务层不允许导入适配器。适配器只把它当作调用参数（`LLMProvider.structured`），
不持有具体 schema 的副本。

schema 只做**结构**约束：字段缺失、类型错误会让整批失败（可诊断、可重试）；
业务规则（越界消息、成员上限、理由为空、分数越界）由服务层逐条丢弃并计数，
避免一条脏数据把整批结果判死。
"""

from __future__ import annotations

from pydantic import BaseModel


class DiscoveryReasonSchema(BaseModel):
    """候选的一条推荐理由。"""

    kind: str = ""  # interesting / important；服务层归一化后过滤未知取值
    text: str = ""  # 具体说明；为空时该条理由被丢弃


class DiscoveryCandidateSchema(BaseModel):
    """模型给出的一个候选事件。"""

    # 只能引用本批输入里的消息 ID；越界会在服务层被丢弃并计数。
    member_message_ids: list[str]
    title: str
    summary: str = ""
    event_time: int | None = None  # UTC 秒；缺失时由成员时间推断
    score: int = 3  # 推荐度，服务层截断到 1–5
    reasons: list[DiscoveryReasonSchema] = []


class DiscoveryDigest(BaseModel):
    """一次发现批次（一批消息）的完整输出。

    `candidates` 必填：缺少字段说明模型没有按约定作答，整批判为无效输出而不是
    "没有值得写的选题"——两者在界面上必须区分。
    """

    candidates: list[DiscoveryCandidateSchema]


class MaterialQuerySchema(BaseModel):
    """资料准备的一条检索式。"""

    text: str
    purpose: str = ""  # 用途说明，便于人工复核"为什么搜这个"


class MaterialQueries(BaseModel):
    """资料准备的检索式列表（数量上限由服务层按预算截断）。"""

    queries: list[MaterialQuerySchema]


class FactSupportSchema(BaseModel):
    """一条引文候选：模型必须原样摘录资料正文里的片段。"""

    material_id: str
    quote: str


class FactSchema(BaseModel):
    """一条关键事实/分析/推测及其引文。"""

    statement: str
    kind: str = "fact"  # fact / analysis / speculation；服务层归一化
    supports: list[FactSupportSchema] = []


class ConflictSchema(BaseModel):
    """资料之间的分歧：涉及第几条 facts（从 0 开始）。"""

    text: str
    statement_indexes: list[int] = []


class GapSchema(BaseModel):
    """关键信息缺口；blocking=true 表示不解决就不该动笔写主线。"""

    text: str
    blocking: bool = False


class MaterialDigest(BaseModel):
    """一次资料整理的完整输出。"""

    facts: list[FactSchema]
    conflicts: list[ConflictSchema] = []
    gaps: list[GapSchema] = []


class OutlineSectionSchema(BaseModel):
    """提纲的一节：小标题、要点、依据事实与引文、行业影响分析。

    `fact_ids`/`evidence_ids` 只能是本项目已核对事实与其引文的 ID；越界、指向
    `missing` 事实或与所选事实无关的条目由服务层逐条丢弃并计数（见 domain/outline_rules）。
    """

    heading: str = ""
    talking_points: list[str] = []
    fact_ids: list[str] = []
    evidence_ids: list[str] = []
    impact_notes: list[str] = []


class OutlineDraft(BaseModel):
    """一次提纲生成的完整输出。

    `sections` 必填：缺少字段说明模型没有按约定作答，整批判为无效输出，而不是
    "这个选题写不出提纲"——两者在界面上必须区分。
    """

    title: str = ""
    angle: str = ""
    sections: list[OutlineSectionSchema]
