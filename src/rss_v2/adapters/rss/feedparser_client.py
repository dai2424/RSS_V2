"""基于 HTTPX 和 feedparser 的 RSS 适配器。"""

from __future__ import annotations

import calendar
import hashlib
import html
import time
from collections.abc import Callable
from typing import Any, cast

import feedparser
import httpx
from bs4 import BeautifulSoup

from rss_v2.domain import ExternalServiceError, FeedItem, FeedSnapshot, SourceLanguage


def _text(value: Any) -> str:
    """将 RSS HTML 字段转成安全纯文本。"""

    if value is None:
        return ""
    return BeautifulSoup(html.unescape(str(value)), "html.parser").get_text(" ", strip=True)


def _language(text: str) -> SourceLanguage:
    """用轻量字符比例判断条目语言，未知内容归为 mixed。"""

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


class HTTPXFeedClient:
    """下载并解析 RSS/Atom feed。"""

    def fetch(self, url: str, timeout_seconds: float) -> FeedSnapshot:
        started = time.perf_counter()
        try:
            response = httpx.get(
                url,
                timeout=timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "RSS-v2/0.1 (+https://localhost)"},
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ExternalServiceError("feed_timeout", "RSS 请求超时") from exc
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError(
                "feed_http_error",
                f"RSS 返回 HTTP {exc.response.status_code}",
                exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError("feed_network_error", "RSS 网络请求失败") from exc
        # feedparser 的 FeedParserDict 是动态 Mapping；此处是适配器边界收窄。
        # 第三方包没有类型存根，显式声明这里的动态函数签名。
        parse_feed = cast(Callable[[bytes], object], feedparser.__dict__["parse"])
        parsed = cast(dict[str, Any], parse_feed(response.content))
        entries = list(parsed.get("entries", []))
        if not parsed.get("version") or (bool(parsed.get("bozo", False)) and not entries):
            raise ExternalServiceError("feed_parse_error", "RSS 内容无法解析")
        items: list[FeedItem] = []
        for raw_entry in entries:
            entry = cast(dict[str, Any], raw_entry)
            title = _text(entry.get("title"))
            summary = _text(entry.get("summary"))
            content_values = cast(list[dict[str, Any]], entry.get("content") or [])
            content = _text(content_values[0].get("value")) if content_values else summary
            link = str(entry.get("link") or "")
            external_id = str(
                entry.get("id")
                or entry.get("guid")
                or link
                or hashlib.sha256(title.encode()).hexdigest()
            )
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            published_seconds = calendar.timegm(published) if published else None
            content_hash = hashlib.sha256(f"{title}\n{summary}\n{content}".encode()).hexdigest()
            items.append(
                FeedItem(
                    external_id=external_id,
                    title=title,
                    summary=summary,
                    content=content,
                    url=link,
                    published_at=published_seconds,
                    language=_language(f"{title} {summary} {content}"),
                    content_hash=content_hash,
                )
            )
        feed = cast(dict[str, Any], parsed.get("feed", {}))
        known = {"title", "link", "description", "language", "author", "updated"}
        metadata: dict[str, Any] = {
            str(key): str(value)
            for key, value in feed.items()
            if key not in known and isinstance(value, (str, int, float, bool))
        }
        metadata["duration_ms"] = int((time.perf_counter() - started) * 1000)
        metadata["http_status"] = response.status_code
        metadata["feed_type"] = str(parsed.get("version"))
        return FeedSnapshot(
            title=_text(feed.get("title")) or None,
            link=str(feed.get("link")) if feed.get("link") else None,
            description=_text(feed.get("description")) or None,
            language=str(feed.get("language")) if feed.get("language") else None,
            author=_text(feed.get("author")) or None,
            updated_at=str(feed.get("updated")) if feed.get("updated") else None,
            metadata=metadata,
            items=items,
        )
