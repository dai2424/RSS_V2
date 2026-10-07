"""SQLite 适配器共用工具。"""

from __future__ import annotations

import json
from typing import Any, cast

from rss_v2.domain.values import new_id as new_id
from rss_v2.domain.values import now as now


def dumps(value: Any) -> str:
    """稳定序列化 JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None) -> dict[str, Any]:
    """读取 JSON 对象，空值返回空对象。"""

    if not value:
        return {}
    result = json.loads(value)
    # isinstance 已确认 JSON 顶层为字典，键由 JSON 解析器保证是字符串。
    return cast(dict[str, Any], result) if isinstance(result, dict) else {}
