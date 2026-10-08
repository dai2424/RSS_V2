"""领域值规范化：标识、UTC 秒、稳定 URL 和内容指纹。"""

import hashlib
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rss_v2.domain.errors import DomainError
from rss_v2.domain.models import SourceLanguage


def message_content_hash(title: str, summary: str, content: str | None) -> str:
    """计算消息版本的内容指纹。

    content 为 ``None`` 表示来源没有提供独立正文（只有描述或摘要），此时用摘要作为
    指纹依据。这样"来源后来补上正文"才会改变指纹，"采集器不再把摘要复制进正文"不会，
    归一历史数据时存量消息不会被判定为内容变化而生成假新版本。
    """
    basis = summary if content is None else content
    return hashlib.sha256(f"{title}\n{summary}\n{basis}".encode()).hexdigest()


def detect_language(text: str) -> SourceLanguage:
    """用字符比例判定中文、英文或混合语言；无字母时保留未知状态。"""
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return SourceLanguage.AUTO
    cjk = sum("\u4e00" <= char <= "\u9fff" for char in letters)
    ascii_letters = sum(char.isascii() for char in letters)
    if cjk / len(letters) > 0.25 and ascii_letters / len(letters) < 0.7:
        return SourceLanguage.CHINESE
    if ascii_letters / len(letters) > 0.7:
        return SourceLanguage.ENGLISH
    return SourceLanguage.MIXED


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


def mask_secret(secret: str) -> str:
    """把密钥转成可展示的掩码标签；界面、审计与日志禁止出现密钥值。"""
    value = secret.strip()
    if len(value) <= 8:
        return "•" * max(len(value), 4)
    return "•" * 8 + value[-4:]


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
