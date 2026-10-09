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
