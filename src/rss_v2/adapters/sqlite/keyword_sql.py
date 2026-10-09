"""关键词的共享 SQL 片段与匹配模式。

检索、相关消息、统计与合并都引用同一份定义：片段的第二份拷贝会让"当前生效的加工结果"
或"规范词键"在不同页面出现不同口径。
"""

from __future__ import annotations

#: LIKE 默认把 % 与 _ 当通配符；用户输入必须按字面匹配，因此统一转义并声明 ESCAPE。
ESCAPE = "\\"

#: 某个版本"当前生效"的加工结果：最近一次成功的那套。检索、相关消息与列表展示共用同一条
#: 定义，否则历史提示词版本产出的关键词会继续参与匹配，换提示词后的结果无法收敛。
CURRENT_ENRICHMENT = (
    "(SELECT e.id FROM message_enrichments e"
    " WHERE e.message_version_id = v.id AND e.status = 'succeeded'"
    " ORDER BY e.updated_at DESC LIMIT 1)"
)

#: 最新版本：列表、相关消息都只比较每条消息的最新版本。
LATEST_VERSION = (
    "(SELECT v2.id FROM message_versions v2"
    " WHERE v2.message_id = m.id ORDER BY v2.version_number DESC LIMIT 1)"
)

#: 指定消息 id 的最新版本，用于没有 messages 别名的子查询。
LATEST_VERSION_OF_MESSAGE = (
    "(SELECT v2.id FROM message_versions v2 WHERE v2.message_id = ?"
    " ORDER BY v2.version_number DESC LIMIT 1)"
)

#: 规范词键：别名解析只有这一个定义，检索、相关消息与统计都引用它。
#: 使用它的查询必须 LEFT JOIN keyword_aliases a ON a.alias_norm = k.normalized。
RESOLVED_KEY = "COALESCE(a.canonical_norm, k.normalized)"

#: 相关度计分：实体重合比主题与事件更能说明"说的是同一件事"。
KIND_WEIGHT = {"entity": 4, "topic": 2, "event": 1}


def escape_like(value: str) -> str:
    """反斜杠、百分号与下划线都按字面处理；调用方的 SQL 必须带 ESCAPE。"""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(value: str) -> str:
    """包含匹配的转义模式。"""

    return f"%{escape_like(value.strip())}%"


def prefix_pattern(value: str) -> str:
    """前缀匹配的转义模式。

    关键词用前缀而不是包含匹配：输入"尊界"要能命中"尊界v800"（同一主体的不同粒度），
    但"ai"不该命中"openai"——那正是旧实现里把 LIKE 打在 JSON 文本上的误命中。
    """

    return f"{escape_like(value)}%"
