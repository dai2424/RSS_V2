"""阶段 4 生成任务的结构化输出 schema（Pydantic）。

放在 domain 而不是适配器里：服务层要按 schema 校验模型输出并据此过滤越界引用，
而服务层不允许导入适配器。适配器只把它当作调用参数（`LLMProvider.structured`），
不持有具体 schema 的副本。

schema 只做**结构**约束：字段缺失、类型错误会让整批失败（可诊断、可重试）；
业务规则（越界消息、成员上限、理由为空、分数越界）由服务层逐条丢弃并计数，
避免一条脏数据把整批结果判死。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator


def _as_text(value: object) -> str | None:
    """把模型给的可选文本归一成字符串：标量转字符串，null 与容器按"没给"处理。

    真实环境实测：模型会把"我不知道"写成 `null`，也会把 id 写成数字或对象。这类
    脏数据应该被服务端丢弃并计数，而不是让整份输出校验失败（`article_rules` 会按
    "不是有效 id"丢掉它们）。
    """

    if value is None or isinstance(value, (dict, list)):
        return None
    return str(value)


#: 宽松的可选文本：模型输出里的各个字符串字段都用它，容错在结构层完成。
TextValue = Annotated[str | None, BeforeValidator(_as_text)]


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


class ArticleParagraphSchema(BaseModel):
    """全文的一段：文本 + 类型。

    `kind` 是段落级的：仅凭 section 无法表达"这一节里哪段是事实、哪段是推测"，
    而"事实段必须带引用、推测段必须带不确定表述"都是按段落判定的（P5 校验原则 3）。

    可选字段允许 `None`：真实模型会把"我不知道"写成 `null`，一个 null 不该让整篇
    生成失败——服务端把它当作"没给"处理（见 article_rules）。
    """

    text: TextValue = ""
    kind: TextValue = "fact"  # fact / analysis / speculation；服务层归一化


class ArticleSectionSchema(BaseModel):
    """全文的一节。"""

    heading: TextValue = ""
    paragraphs: list[ArticleParagraphSchema] = []


class ArticleCitationSchema(BaseModel):
    """正文一个引用标记对应的事实与引文。

    `marker` 与正文里的 `[^n]` 对应；模型只能给 ID，服务端负责核对归属并快照。
    """

    marker: TextValue = ""  # 形如 "1"；正文里写 [^1]
    fact_id: TextValue = ""
    evidence_ids: list[TextValue] = []


class ArticleSourceSchema(BaseModel):
    """模型给出的来源候选。

    只采信 `material_id`：`title`/`publisher`/`url` 一律由服务端从资料行填充，
    模型自造的地址在结构上不可能进入成稿。
    """

    material_id: TextValue = None
    title: TextValue = None
    publisher: TextValue = None
    url: TextValue = None


class ArticleDraft(BaseModel):
    """一次全文生成的完整输出。

    `sections` 必填：缺失说明模型没有按约定作答，整次生成失败，而不是"写不出来"。
    """

    title: TextValue = ""
    lead: TextValue = ""
    sections: list[ArticleSectionSchema]
    citations: list[ArticleCitationSchema] = []
    source_list: list[ArticleSourceSchema] = []
