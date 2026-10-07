"""SQLite 适配器共用工具。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, cast


def new_id() -> str:
    """返回 UUID 字符串。"""

    return str(uuid.uuid4())


def now() -> int:
    """返回 UTC Unix 秒。"""

    return int(time.time())


def dumps(value: Any) -> str:
    """稳定序列化 JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None) -> dict[str, Any]:
    """读取 JSON 对象，空值返回空对象。"""

    if not value:
        return {}
    result = json.loads(value)
    return cast(dict[str, Any], result) if isinstance(result, dict) else {}
