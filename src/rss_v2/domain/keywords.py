"""关键词的领域逻辑：类型、匹配键与准入约束。

归一化只做**字面等价**（全角折半角、大小写、空白、首尾标点），不做任何语义推断：
"两个词指同一件事"是人工在词表上确认的结论，程序不猜。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class KeywordKind(StrEnum):
    """关键词类型；决定检索排序时的优先级。"""

    ENTITY = "entity"  # 具名实体：公司、产品、模型、人物、机构、地点、法规、专有技术名
    TOPIC = "topic"  # 能概括话题的领域词
    EVENT = "event"  # 用一句话概括的事件，一条消息最多一个


#: 类型的中文名；注入词表与批次文本都用它，避免两处各写一套。
KEYWORD_KIND_LABELS: dict[KeywordKind, str] = {
    KeywordKind.ENTITY: "实体",
    KeywordKind.TOPIC: "主题",
    KeywordKind.EVENT: "事件",
}

#: 每条消息的关键词上限，与提示词里的"最多 6 个"保持一致。
KEYWORD_LIMIT = 6

#: 句子式写法的标点：出现任意一个就说明这是"一句话"，不是检索词。
#: 刻意不含 ASCII 句点与冒号——「GPT-6.1」「19.98万元」里的点不是句读。
_SENTENCE_PUNCTUATION = re.compile(r"[，。！？；、,;!?]")

#: 纯数值/价格/数量的形态：整条写法只由数字、分隔符与这些单位组成才算。
#: 刻意不做"去掉数字后剩几个字符"的粗判——那会把 H100、A100、B200、5G 这类
#: 型号与制式一起删掉，而它们恰恰是检索价值最高的写法之一。
#: 单字母单位（N/V/A/W）刻意不收：它们与型号撞名，A100、V100 明显更值得保留。
_QUANTITY_UNITS = (
    "万元|亿元|万亿|万|亿|千|百|元|美元|欧元|日元|人民币|个百分点|%|％|折|起|约|左右|上下"
    "|以内|以上|千瓦|兆瓦|瓦|度|吨|公斤|克|公里|千米|米|厘米|毫米|秒|分钟|小时|天|年|月|日|号"
    "|MPa|GPa|kPa|GB|TB|MB|KB|GHz|MHz|kHz|Hz|kW|MW|km|cm|mm|ml|kg|℃"
)
_QUANTITY_SHAPE = re.compile(
    rf"^[\d\s.,，、\-~－—至到:：/]*"
    rf"(?:{_QUANTITY_UNITS})?"
    rf"(?:[\d\s.,，、\-~－—至到:：/]*(?:{_QUANTITY_UNITS})?)*$"
)

#: 写法的长度上限（字符）：再长就不会有人用来检索，多半是模型把一句话塞了进来。
MAXIMUM_KEYWORD_LENGTH = 24

#: 归一化时剥离的首尾标点、符号与下划线；内部标点保留。
_EDGE_PUNCTUATION = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)


def normalize_keyword(text: str) -> str:
    """把关键词压成用于匹配的形式。

    步骤：Unicode NFKC（全角折半角）→ 大小写折叠 → 去掉所有空白 → 剥离首尾标点。
    中文没有词间空格，去掉空白让"AI 助手"与"AI助手"落在同一个匹配键上；内部标点保留，
    避免把 GPT-6.1 与 GPT6.1 这类本来不同的写法合并。空串表示这个词没有匹配价值。
    """

    folded = unicodedata.normalize("NFKC", text).casefold()
    compact = "".join(folded.split())
    return _EDGE_PUNCTUATION.sub("", compact)


def keyword_rejection_reason(text: str) -> str | None:
    """判断一个写法为什么不能作为检索词；可以收下时返回 None。

    只判定**形态**：句子式（带句读标点）、只剩数字与单位（价格、指标）、超长写法，
    这三类不可能被人用来检索。"这个词是否泛化"属于语义判断，不在这里做。
    """

    stripped = text.strip()
    if _SENTENCE_PUNCTUATION.search(stripped):
        return "句子式写法"
    if len(stripped) > MAXIMUM_KEYWORD_LENGTH:
        return "过长"
    if any(char.isdigit() for char in stripped) and _QUANTITY_SHAPE.match(stripped):
        return "只有数值与单位"
    return None


@dataclass(frozen=True, slots=True)
class Keyword:
    """一条检索关键词。

    text 是模型给出的原始写法，只用于展示；normalized 是匹配键，检索、去重与统计只用它。
    两者分离是刻意的：归一化必然有算错的时候，展示层不能跟着错。
    """

    text: str  # 展示用原文，保持模型写法
    kind: KeywordKind  # entity / topic / event
    reused: bool | None = None  # 是否复用了注入词表的写法；为空表示当时没有词表可判定

    @property
    def normalized(self) -> str:
        """匹配键；由 text 实时计算，不单独存储，避免两处不一致。"""

        return normalize_keyword(self.text)


#: 词表为空时的注入文案：没有可复用的写法，让模型自己建。
EMPTY_VOCABULARY_TEXT = "（当前词表为空，没有可复用的写法）"


@dataclass(frozen=True, slots=True)
class PromptVocabulary:
    """注入提示词的词表快照。

    存的是**展示写法**而不是归一化键：模型能直接复用的是写法，判定复用时再归一化一次
    即可对齐。常驻词由调用方并进它所属的类型分组，避免同一个词在提示词里出现两次。
    """

    entities: tuple[str, ...]  # 具名实体的写法
    topics: tuple[str, ...]  # 领域主题的写法
    events: tuple[str, ...]  # 事件词的写法
    recent: tuple[str, ...]  # 近 N 天新增、且没进前面分组的写法

    @property
    def empty(self) -> bool:
        """没有任何词时为真；空词表不渲染分组标题。"""

        return not (self.entities or self.topics or self.events or self.recent)

    @property
    def terms(self) -> frozenset[str]:
        """全部注入词的匹配键，用于判定模型是否复用了词表。"""

        grouped = (*self.entities, *self.topics, *self.events, *self.recent)
        return frozenset(key for text in grouped if (key := normalize_keyword(text)))

    @property
    def fingerprint(self) -> str:
        """注入文本的指纹；词表内容（新增词、合并、注入条数）变化时才会变。"""

        return hashlib.sha256(render_vocabulary(self).encode("utf-8")).hexdigest()[:16]


def render_vocabulary(vocabulary: PromptVocabulary) -> str:
    """把词表渲染成提示词片段。

    组内按写法排序而不是按频次：注入只保证"这个词可见"，不表达重要度，排序固定才能让
    同一份词表两次渲染得到完全相同的文本，`llm_calls.input_hash` 才解释得清。
    """

    if vocabulary.empty:
        return EMPTY_VOCABULARY_TEXT
    groups = (
        (KEYWORD_KIND_LABELS[KeywordKind.ENTITY], vocabulary.entities),
        (KEYWORD_KIND_LABELS[KeywordKind.TOPIC], vocabulary.topics),
        (KEYWORD_KIND_LABELS[KeywordKind.EVENT], vocabulary.events),
        ("近期新增", vocabulary.recent),
    )
    return "\n".join(
        f"{label}：{'、'.join(sorted(set(texts)))}" for label, texts in groups if texts
    )


@dataclass(frozen=True, slots=True)
class RelatedMessage:
    """与某条消息共享关键词的另一条消息。"""

    message_id: str  # 另一条消息 UUID
    source_id: str  # 该消息所属来源 UUID
    version_id: str  # 参与比较的最新版本 UUID
    title: str  # 该版本的标题
    published_at: int | None  # 发布时间，UTC 秒；来源未提供时为空
    collected_at: int  # 采集时间，UTC 秒
    shared: tuple[Keyword, ...]  # 双方共有的关键词，实体优先


def normalize_keyword_list(
    items: Iterable[Keyword], limit: int = KEYWORD_LIMIT
) -> tuple[Keyword, ...]:
    """去空、按匹配键去重、限流，并保证事件词最多一个。

    顺序沿用模型给出的次序（即重要度次第）。超出的第三个以上事件词降级为主题词而不是
    直接丢弃：它们是模型认为相关的词，只是不该占据"事件"这一格。
    """

    result: list[Keyword] = []
    seen: dict[str, None] = {}
    event_used = False
    for item in items:
        normalized = item.normalized
        if not normalized or normalized in seen:
            continue
        kind = item.kind
        if kind is KeywordKind.EVENT:
            if event_used:
                kind = KeywordKind.TOPIC
            else:
                event_used = True
        seen[normalized] = None
        result.append(Keyword(text=item.text.strip(), kind=kind))
        if len(result) >= limit:
            break
    return tuple(result)


@dataclass(frozen=True, slots=True)
class MergePreview:
    """一次合并的影响面；预览与执行共用同一份计算，避免两处口径不一致。"""

    sources: tuple[str, ...]  # 将被并入的规范词键
    target: str  # 目标规范词键
    forms: tuple[Keyword, ...]  # 合并后该词条下的全部写法
    mentions: int  # 合并后的出现次数（词位）
    messages: int  # 受影响的消息数（按版本去重）


@dataclass(frozen=True, slots=True)
class MergeRecord:
    """一次合并的记录；已撤销的记录保留下来供审计。"""

    id: str  # 合并 UUID
    target_key: str  # 合并后的规范词键
    target_raw: str  # 当时的展示写法
    members: tuple[Keyword, ...]  # 合并时被并入的写法快照（不可变，只用于展示）
    messages: int  # 合并时受影响的消息数
    created_at: int  # 创建时间，UTC 秒
    undone_at: int | None  # 撤销时间，UTC 秒；为空表示仍然生效


@dataclass(frozen=True, slots=True)
class KeywordEntry:
    """词表里的一行：一个规范词及其规模。"""

    key: str  # 规范词键，界面回传它做筛选与合并
    text: str  # 展示写法：该词条下出现最多的那种
    kind: KeywordKind  # 词条类型，按实体优先于主题、主题优先于事件裁决
    mentions: int  # 出现次数，按消息版本去重
    sources: int  # 覆盖的来源数
    first_seen_at: int  # 首次出现时间，UTC 秒
    last_seen_at: int  # 最近出现时间，UTC 秒
    aliases: tuple[str, ...]  # 归并到这个词条的其它写法（原始文本，含被合并进来的）
    pinned: bool = False  # 人工标记的常驻词：注入提示词时必然出现


@dataclass(frozen=True, slots=True)
class SuggestedGroup:
    """模型给出的一组疑似同指写法（服务端已按批次过滤前的原始结果）。"""

    members: tuple[str, ...]  # 组内写法，模型输出原文
    target: str  # 建议保留的写法
    rationale: str  # 为什么认为是同指


@dataclass(frozen=True, slots=True)
class SuggestResult:
    """一次合并建议调用的结构化结果。"""

    groups: tuple[SuggestedGroup, ...]


@dataclass(frozen=True, slots=True)
class MergeSuggestion:
    """一条待人工确认的合并建议。"""

    id: str  # 建议 UUID
    fingerprint: str  # 成员集合指纹：同一组不再重复建议，忽略过的也不会回来
    members: tuple[Keyword, ...]  # 组内写法；text 展示、normalized 是规范词键
    target_key: str  # 建议保留的规范词键，必是成员之一
    target_text: str  # 目标词的展示写法
    rationale: str  # 模型给出的理由
    status: str  # pending / accepted / dismissed
    batch_offset: int  # 生成时在词表里的起始偏移，用于追批次
    created_at: int  # 创建时间，UTC 秒
    resolved_at: int | None  # 处理时间，UTC 秒；待处理时为空
    merge_id: str | None  # 采纳后关联的合并记录 UUID


@dataclass(frozen=True, slots=True)
class SuggestedPin:
    """模型从跨周候选里挑出的一个常驻建议。"""

    term: str  # 候选里的写法，模型输出原文
    rationale: str  # 为什么值得常驻


@dataclass(frozen=True, slots=True)
class PinSuggestResult:
    """一次常驻建议调用返回的结构化结果。"""

    picks: tuple[SuggestedPin, ...]


@dataclass(frozen=True, slots=True)
class PinCandidate:
    """跨周反复出现的候选词：常驻建议的输入。"""

    key: str  # 规范词键
    text: str  # 展示写法
    weeks: int  # 最近 4 周里出现过几周


@dataclass(frozen=True, slots=True)
class PinSuggestion:
    """一条待人工确认的常驻建议。"""

    key: str  # 规范词键
    text: str  # 展示写法
    rationale: str  # 模型给出的理由
    weeks: int  # 候选依据：跨了几周
    status: str  # pending / accepted / dismissed
    created_at: int  # 创建时间，UTC 秒
    resolved_at: int | None  # 处理时间，UTC 秒；待处理为空


@dataclass(frozen=True, slots=True)
class PinSuggestionBatch:
    """一次生成常驻建议的结果概要。"""

    candidates: int  # 跨周候选数（模型实际看到的）
    picks: int  # 落库的建议数
    dropped: int  # 被丢弃的条数（越界写法、已常驻、已建议过）


@dataclass(frozen=True, slots=True)
class SuggestionEnqueue:
    """一次入队的结果：新排了几个批次、命中几个已存在的任务。"""

    enqueued: int  # 新创建的任务数
    queued_words: int  # 本次排进队列的词数


@dataclass(frozen=True, slots=True)
class SuggestionStatus:
    """建议的当前状态：任务进度与待确认数量。"""

    queued: int  # 排队中的建议任务
    running: int  # 运行中的建议任务
    failed: int  # 失败待处理的建议任务
    pending: int  # 待人工确认的建议组
    total: int  # 词表词数
    scanned: int  # 交给模型看过至少一次的写法数量
    last_scanned_at: int | None  # 最近一次扫描时间，UTC 秒；从没扫过时为空


@dataclass(frozen=True, slots=True)
class SuggestionBatch:
    """一次生成的结果概要；已生成过的批次直接回放，不重复调用模型。"""

    vocabulary_fingerprint: str  # 本批词表的内容指纹（同一批词只处理一次）
    size: int  # 本批实际取的词数
    total: int  # 词表总词数
    groups: int  # 本批落库的建议组数
    dropped: int  # 被丢弃的组数（越界写法、已建议过、已同指等）


@dataclass(frozen=True, slots=True)
class KeywordKindStat:
    """某一类型的关键词规模。"""

    kind: KeywordKind  # 类型
    terms: int  # 该类型的规范词数
    mentions: int  # 该类型的词位数


@dataclass(frozen=True, slots=True)
class KeywordBucket:
    """长尾分布的一档，例如"出现 2 次"或"出现 6 次以上"。"""

    label: str  # 档位说明
    terms: int  # 该档的规范词数


@dataclass(frozen=True, slots=True)
class KeywordTrendPoint:
    """某一天的关键词产出。"""

    day: str  # UTC 日期，YYYY-MM-DD
    mentions: int  # 当天消息产出的词位数
    new_terms: int  # 当天首次出现的规范词数


@dataclass(frozen=True, slots=True)
class KeywordSourceStat:
    """来源维度：某个来源贡献的关键词规模。"""

    source_id: str  # 来源 UUID
    source_name: str  # 来源名称
    terms: int  # 该来源出现的规范词数
    mentions: int  # 词位数
    top: tuple[Keyword, ...]  # 该来源出现最多的写法


@dataclass(frozen=True, slots=True)
class KeywordOverview:
    """关键词总体情况；所有数字都从库里算出，不含估算。"""

    messages: int  # 有最新版本的消息数
    enriched: int  # 其中有生效加工结果的
    pending: int  # 还没有加工结果、可回填的
    hot_days: int  # 热词窗口（天）
    hot_min_mentions: int  # 热词门槛（窗口内出现次数）
    terms: int  # 热词窗口内的规范词数
    mentions: int  # 热词窗口内的词位数
    singletons: int  # 窗口内只出现一次的规范词数
    terms_all: int  # 全量规范词数（存量参考）
    mentions_all: int  # 全量词位数（存量参考）
    singletons_all: int  # 全量只出现一次的规范词数
    average_per_message: float  # 窗口内平均每条已加工消息的词位数（不套门槛）
    aliases: int  # 已经并入别处的写法数
    merges: int  # 生效中的合并数
    reused: int  # 判定为复用了注入词表的词位数
    reuse_judged: int  # 有复用判定的词位数（注入上线前的旧数据没有判定）
    reuse_rate: float | None  # 复用率 = reused / reuse_judged；没有判定数据时为空
    vocab_revision: int  # 当前词表版本；注入内容变化时递增，0 表示还没有加工过
    pinned: int  # 人工标记的常驻词数
    suggestions_pending: int  # 待人工处置的合并建议数
    kinds: tuple[KeywordKindStat, ...]  # 类型构成
    long_tail: tuple[KeywordBucket, ...]  # 长尾分布
    trend: tuple[KeywordTrendPoint, ...]  # 按天的产出与新增词
    sources: tuple[KeywordSourceStat, ...]  # 来源分布
    tasks_queued: int  # 加工任务：排队中
    tasks_running: int  # 加工任务：运行中
    tasks_succeeded: int  # 加工任务：已完成
    tasks_failed: int  # 加工任务：失败
