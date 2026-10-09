"""关键词的领域逻辑：类型、匹配键与准入约束。

归一化只做**字面等价**（全角折半角、大小写、空白、首尾标点），不做任何语义推断：
"两个词指同一件事"是人工在词表上确认的结论，程序不猜。
"""

from __future__ import annotations

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


#: 每条消息的关键词上限，与提示词里的"最多 6 个"保持一致。
KEYWORD_LIMIT = 6

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


@dataclass(frozen=True, slots=True)
class Keyword:
    """一条检索关键词。

    text 是模型给出的原始写法，只用于展示；normalized 是匹配键，检索、去重与统计只用它。
    两者分离是刻意的：归一化必然有算错的时候，展示层不能跟着错。
    """

    text: str  # 展示用原文，保持模型写法
    kind: KeywordKind  # entity / topic / event

    @property
    def normalized(self) -> str:
        """匹配键；由 text 实时计算，不单独存储，避免两处不一致。"""

        return normalize_keyword(self.text)


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
    terms: int  # 规范词数
    mentions: int  # 词位总数
    singletons: int  # 只出现一次的规范词数
    average_per_message: float  # 平均每条已加工消息的词位数
    aliases: int  # 已经并入别处的写法数
    merges: int  # 生效中的合并数
    kinds: tuple[KeywordKindStat, ...]  # 类型构成
    long_tail: tuple[KeywordBucket, ...]  # 长尾分布
    trend: tuple[KeywordTrendPoint, ...]  # 按天的产出与新增词
    sources: tuple[KeywordSourceStat, ...]  # 来源分布
    tasks_queued: int  # 加工任务：排队中
    tasks_running: int  # 加工任务：运行中
    tasks_succeeded: int  # 加工任务：已完成
    tasks_failed: int  # 加工任务：失败
