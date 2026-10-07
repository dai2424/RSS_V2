"""领域值规范化：标识、UTC 秒和稳定 URL。"""

import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rss_v2.domain.errors import DomainError


def validate_http_url(value: str) -> str:
    """校验 HTTP(S) 地址；认证信息必须通过密钥引用传递。"""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise DomainError("invalid_url", "地址必须是有效的 HTTP(S) 地址，且不能包含认证信息")
    return value.strip()


def new_id() -> str:
    """生成 UUID 字符串。"""
    return str(uuid.uuid4())


def now() -> int:
    """返回 UTC Unix 秒。"""
    return int(time.time())


def normalize_url(value: str) -> str:
    """去掉 fragment 和常见跟踪参数，保留业务查询参数。"""
    parsed = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            urlencode(sorted(query)),
            "",
        )
    )
