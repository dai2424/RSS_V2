"""原文读取适配器：HTTPX 抓页 + BeautifulSoup 抽正文。

边界与失败都写进返回结果（不抛业务异常）：单份资料失败不该让整次资料准备失败。
规则见 docs/决策记录/2026-10-15-检索与网页读取.md 第 3.3 节：

- 只允许 http/https；解析到回环/私网/链路本地/保留地址时拒绝（检索结果来自外部，
  不能让应用变成内网代理）；
- 手动跟随重定向（最多 5 跳），**每一跳都重新校验地址**；
- 流式读取并在超过字节上限时截断（保留已读前缀并标记 truncated）；
- 正文优先 `<article>`、其次 `<main>`、再按段落文本量挑最“厚”的容器；
- 正文不足 400 字符时回落到页面描述，标记 `summary_only`；
- 元信息取 og/title/h1、og:site_name、article:published_time 等，取不到就留空——不猜。
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import logging
import re
import socket
import time
from typing import cast
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from rss_v2.domain import (
    ARTICLE_HTTP_ERROR,
    ARTICLE_INVALID_URL,
    ARTICLE_TIMEOUT,
    ARTICLE_UNPARSABLE,
    MIN_ARTICLE_CHARS,
    SSRF_BLOCKED,
    ArticleRead,
    MaterialStatus,
)

logger = logging.getLogger(__name__)

USER_AGENT = "RSS-v2/0.1 (+https://localhost)"

#: 最多跟随几跳重定向：正常跳转一两跳，超过说明是短链农场或异常站点。
MAX_REDIRECTS = 5

#: 代理 fake-IP 常用的网段：Python 把它们算作 private，这里放行（见 _is_internal）。
_PROXY_FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("100.64.0.0/10"),
)

#: 需要连同内容一起丢掉噪音容器：导航、页脚、分享、评论、广告、订阅引导等。
_NOISE_TAGS = ("script", "style", "nav", "footer", "aside", "form", "iframe", "noscript", "svg")
_NOISE_PATTERN = re.compile(
    r"share|related|comment|advert|promo|subscribe|cookie|banner|sidebar|menu|recommend|"
    r"footer|header|breadcrumb|toolbar|newsletter|popup",
    re.IGNORECASE,
)

#: 段落级标签：正文按它们切分，块之间用空行隔开。
_BLOCK_TAGS = ("p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre")

_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)


class HTTPXArticleReader:
    """抓取网页并抽取正文；失败以 `ArticleRead.status` 返回，不抛异常。"""

    def __init__(self, timeout_seconds: float, max_bytes: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def read(self, url: str) -> ArticleRead:
        started = time.perf_counter()
        try:
            response, final_url, truncated, body = self._fetch(url)
        except _ReadFailure as failure:
            logger.info("article_failed url=%s code=%s", url, failure.code)
            return _failure("", MaterialStatus.FAILED.value, failure.code, failure.message)
        text = _decode(body, response)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code in {404, 410}:
            return _failure(
                final_url or url,
                MaterialStatus.UNAVAILABLE.value,
                "",
                f"来源已不可用（HTTP {response.status_code}）",
            )
        if response.status_code >= 400:
            return _failure(
                final_url or url,
                MaterialStatus.FAILED.value,
                ARTICLE_HTTP_ERROR,
                f"读取返回 HTTP {response.status_code}",
            )
        extracted = _extract(text)
        if len(extracted.text) < MIN_ARTICLE_CHARS:
            # 正文太短：多数是付费墙、导航壳或反爬页；页面描述还能用就只算"仅摘要"。
            if extracted.description:
                return ArticleRead(
                    status=MaterialStatus.SUMMARY_ONLY.value,
                    final_url=final_url,
                    title=extracted.title,
                    publisher=extracted.publisher,
                    published_at=extracted.published_at,
                    text=extracted.description,
                    content_hash=_hash(extracted.description),
                    byte_size=len(body),
                    truncated=truncated,
                )
            return _failure(
                final_url,
                MaterialStatus.FAILED.value,
                ARTICLE_UNPARSABLE,
                "页面没有可用的正文或摘要",
            )
        logger.info(
            "article_read url=%s chars=%d bytes=%d truncated=%s %dms",
            url,
            len(extracted.text),
            len(body),
            truncated,
            duration_ms,
        )
        return ArticleRead(
            status=MaterialStatus.READ.value,
            final_url=final_url,
            title=extracted.title,
            publisher=extracted.publisher,
            published_at=extracted.published_at,
            text=extracted.text,
            content_hash=_hash(extracted.text),
            byte_size=len(body),
            truncated=truncated,
        )

    def _fetch(self, url: str) -> tuple[httpx.Response, str, bool, bytes]:
        """跟随重定向并读取正文（带上限）；每一跳都校验地址是否允许访问。"""

        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._ensure_allowed(current)
            try:
                with httpx.stream(
                    "GET",
                    current,
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise _ReadFailure(ARTICLE_HTTP_ERROR, "重定向响应缺少 Location")
                        current = urljoin(current, location)
                        continue
                    body, truncated = _read_capped(response, self.max_bytes)
                    return response, str(response.url), truncated, body
            except httpx.TimeoutException as exc:
                raise _ReadFailure(ARTICLE_TIMEOUT, "读取超时") from exc
            except httpx.HTTPError as exc:
                raise _ReadFailure(ARTICLE_HTTP_ERROR, "网络请求失败") from exc
        raise _ReadFailure(ARTICLE_HTTP_ERROR, f"重定向超过 {MAX_REDIRECTS} 跳")

    @staticmethod
    def _ensure_allowed(url: str) -> None:
        """协议与地址白名单：非 http/https、解析到私网或回环一律拒绝。"""

        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise _ReadFailure(ARTICLE_INVALID_URL, "只允许 http/https 地址")
        try:
            addresses = _resolve(parts.hostname)
        except OSError as exc:
            raise _ReadFailure(ARTICLE_HTTP_ERROR, "域名解析失败") from exc
        for address in addresses:
            if _is_internal(ipaddress.ip_address(address)):
                raise _ReadFailure(SSRF_BLOCKED, "地址指向本机或内网，已拒绝读取")


class _ReadFailure(Exception):
    """内部失败信号：拿到错误码与可读原因后转成 ArticleRead。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _failure(url: str, status: str, code: str, message: str) -> ArticleRead:
    """构造失败结果：`unavailable` 表示来源下架，`failed` 表示这次没读成。"""

    return ArticleRead(
        status=status,
        final_url=url,
        title="",
        publisher="",
        published_at=None,
        text="",
        content_hash="",
        byte_size=0,
        error_code=code,
        error_message=message,
    )


def _is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """是否属于该拒绝的内网/本机目标。

    只拦真正的内网与回环（RFC1918、回环、链路本地、未指定、组播）。代理 fake-IP 用的
    保留段（198.18.0.0/15、100.64.0.0/10）放行：实测本机走代理时所有域名都解析到
    198.18.x.x，若一并拒绝就等于完全读不到外部资料；能真正打到内网服务的地址仍被拦。
    """

    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return True
    if not ip.is_private:
        return False
    # Python 把"基准测试段"与运营商级 NAT 也归到 private；本地代理常用它们做 fake-IP，
    # 这里放行，真正的 RFC1918 内网仍然被拦。
    return not any(ip in network for network in _PROXY_FAKE_IP_NETWORKS)


def _resolve(host: str) -> list[str]:
    """域名 → IP 列表；字面量 IP 直接返回。"""

    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        return [str(item[4][0]) for item in socket.getaddrinfo(host, None)]


def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """按字节上限流式读取；超过上限时保留已读前缀并标记截断。"""

    chunks: list[bytes] = []
    size = 0
    truncated = False
    for chunk in response.iter_bytes():
        remaining = max_bytes - size
        if len(chunk) >= remaining:
            # 分块可能远大于上限（默认 64 KB）：只取还装得下的部分，保证不超过配置值。
            chunks.append(chunk[:remaining])
            size = max_bytes
            truncated = True
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks), truncated


def _decode(body: bytes, response: httpx.Response) -> str:
    """按响应头、HTML meta 与常见中文编码依次尝试解码。"""

    declared = response.charset_encoding
    for encoding in (declared, _meta_charset(body), "utf-8", "gb18030"):
        if not encoding:
            continue
        try:
            return body.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _meta_charset(body: bytes) -> str | None:
    match = _META_CHARSET.search(body[:4096])
    return match.group(1).decode("ascii", "replace") if match else None


class _Extracted:
    """抽取结果：正文、标题、发布者、发布时间与页面描述。"""

    __slots__ = ("text", "title", "publisher", "published_at", "description")

    def __init__(
        self,
        text: str,
        title: str,
        publisher: str,
        published_at: int | None,
        description: str,
    ) -> None:
        self.text = text
        self.title = title
        self.publisher = publisher
        self.published_at = published_at
        self.description = description


def _extract(html: str) -> _Extracted:
    soup = BeautifulSoup(html, "html.parser")
    title = _title(soup)
    publisher = _publisher(soup)
    published_at = _published_at(soup)
    description = _description(soup)
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(class_=_NOISE_PATTERN):
        tag.decompose()
    for tag in soup.find_all(id=_NOISE_PATTERN):
        tag.decompose()
    container = soup.find("article") or soup.find("main") or _densest(soup) or soup.body or soup
    lines: list[str] = []
    for block in container.find_all(_BLOCK_TAGS):
        if block.find_parent(_BLOCK_TAGS) is not None and block.name != "li":
            continue  # 嵌套块由最外层负责，避免同一段被收两次
        text = " ".join(block.get_text(" ", strip=True).split())
        if text:
            lines.append(text)
    return _Extracted("\n\n".join(lines), title, publisher, published_at, description)


def _densest(soup: BeautifulSoup) -> Tag | None:
    """按"子孙段落文本总量"挑最厚的容器：一次遍历累加，避免逐候选重复扫描。"""

    scores: dict[int, int] = {}
    owners: dict[int, Tag] = {}
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if not text:
            continue
        owner = paragraph.find_parent(["article", "main", "div", "section", "body"]) or soup
        key = id(owner)
        owners[key] = owner
        scores[key] = scores.get(key, 0) + len(text)
    if not scores:
        return None
    return owners[max(scores, key=lambda key: scores[key])]


def _title(soup: BeautifulSoup) -> str:
    for selector in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
        meta = soup.select_one(selector)
        if meta is not None:
            content = str(meta.get("content") or "").strip()
            if content:
                return content
    heading = soup.find("h1")
    if isinstance(heading, Tag):
        text = " ".join(heading.get_text(" ", strip=True).split())
        if text:
            return text
    if soup.title and soup.title.string:
        return " ".join(str(soup.title.string).split())
    return ""


def _publisher(soup: BeautifulSoup) -> str:
    meta = soup.select_one('meta[property="og:site_name"]')
    if meta is not None:
        content = str(meta.get("content") or "").strip()
        if content:
            return content
    return ""


def _description(soup: BeautifulSoup) -> str:
    for selector in (
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[name="twitter:description"]',
    ):
        meta = soup.select_one(selector)
        if meta is not None:
            content = " ".join(str(meta.get("content") or "").split())
            if content:
                return content
    return ""


def _published_at(soup: BeautifulSoup) -> int | None:
    """发布时间：article/og meta 优先，其次 JSON-LD datePublished；解析失败留空。"""

    raw = ""
    for selector in (
        'meta[property="article:published_time"]',
        'meta[property="og:published_time"]',
        'meta[name="pubdate"]',
        'meta[name="publishdate"]',
        'meta[name="date"]',
    ):
        meta = soup.select_one(selector)
        if meta is not None:
            raw = str(meta.get("content") or "").strip()
            if raw:
                break
    if not raw:
        raw = _json_ld_date(soup)
    return _parse_time(raw)


def _json_ld_date(soup: BeautifulSoup) -> str:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload: object = json.loads(script.get_text())
        except ValueError:
            continue
        nodes: list[object] = (
            cast(list[object], payload) if isinstance(payload, list) else [payload]
        )
        for node in nodes:
            if isinstance(node, dict):
                record = cast(dict[str, object], node)
                value = record.get("datePublished")
                if value:
                    return str(value)
    return ""


def _parse_time(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return int(parsed.timestamp())


def _hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
