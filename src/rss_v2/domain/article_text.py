"""全文的文本归一化工具：标记、段落类型与链接协议。

这些函数只处理"一段文字"本身，不涉及资料包或版本；单独成模块便于逐条单测，
也让 `article_rules`（整篇的核对与推导规则）保持在可读的规模内。
"""

from __future__ import annotations

import re

from rss_v2.domain.articles import ParagraphKind

#: 推测段必须包含其中之一，否则降级为分析（契约原则 3 的原文口径）。
#: 只收中文表述：成稿正文要求中文输出，英文措辞不算。
UNCERTAIN_WORDS: tuple[str, ...] = (
    "可能",
    "或许",
    "大概",
    "预计",
    "有望",
    "不排除",
    "或将",
    "推测",
    "尚未可知",
    "尚不确定",
    "难以判断",
    "有待",
    "似乎",
    "看起来",
)

_BARE_MARKER = re.compile(r"\d{1,3}")
#: 链接地址允许一层配对的圆括号：`javascript:alert(1)` 这类地址要整段识别出来，
#: 才能连同"它不安全"的判断一起处理，而不是漏下一个 ")" 留在正文里。
_INLINE_LINK = re.compile(r"\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")


def normalize_marker(raw: str | None) -> str | None:
    """把模型写的标记归一成纯数字（`1`、`[1]`、`^1`、`[^1]` → `"1"`）。

    归一不了、或序号不是正整数时返回 None：这类引用条目丢弃并计数。
    """

    text = str(raw or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    text = text.lstrip("^").strip()
    if not _BARE_MARKER.fullmatch(text) or int(text) < 1:
        return None
    return str(int(text))


def normalize_kind(raw: str | None) -> tuple[str, bool]:
    """段落类型归一化；返回（类型, 是否为未知取值）。"""

    text = str(raw or "").strip().lower()
    for kind in ParagraphKind:
        if text == kind.value:
            return kind.value, False
    return ParagraphKind.ANALYSIS.value, True


def mentions_uncertainty(text: str) -> bool:
    """文本里是否出现不确定表述（推测段的必要条件）。"""

    return any(word in text for word in UNCERTAIN_WORDS)


def clean_links(text: str) -> tuple[str, int]:
    """把非 http/https 的链接还原成纯文字，返回（文本, 被拒绝条数）。

    在生成时做而不是在渲染时做：这样"模型写了 `javascript:` 链接"会作为一次丢弃
    被计数，而渲染器只需要做防御性检查。
    """

    rejected = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal rejected
        target = match.group(2).strip()
        if target.lower().startswith(("http://", "https://")):
            return match.group(0)
        rejected += 1
        return match.group(1)

    return _INLINE_LINK.sub(replace, text), rejected
