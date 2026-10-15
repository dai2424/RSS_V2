"""SearXNG 兼容 JSON 端点的检索适配器。

按官方 Search API（GET /search?q=…&format=json）调用；实测与官方文档有两处要注意：

1. **没有 `limit` 参数**：单页返回几十条（实测 55–84 条），因此"取几条"只能在本地截断；
2. **`publishedDate` 缺失时返回字符串 `"None"`**（不是 null），必须按缺失处理。

部分上游引擎会被限流（brave、google cse 等），此时 `unresponsive_engines` 非空但整体
仍是 200：这属于"这次没问到"，不能当成"没有资料"，因此与命中一起返回给调用方。

官方文档：<https://docs.searxng.org/dev/search_api.html>（`format=json` 需在实例的
`search.formats` 中开启，否则返回 403）。
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import cast

import httpx

from rss_v2.domain import (
    SEARCH_FORMAT_DISABLED,
    SEARCH_INVALID_OUTPUT,
    SEARCH_NETWORK_ERROR,
    SEARCH_RATE_LIMITED,
    SEARCH_SERVER_ERROR,
    SEARCH_TIMEOUT,
    SEARCH_UNCONFIGURED,
    ExternalServiceError,
    SearchHit,
    SearchOutcome,
)

logger = logging.getLogger(__name__)

#: 检索请求的标识：SearXNG 反代/日志里能对上是谁在问。
USER_AGENT = "RSS-v2/0.1 (+https://localhost)"


class HTTPXSearchClient:
    """SearXNG 兼容端点的检索实现；地址为空时明确失败，不用假数据代替。"""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.strip()
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> SearchOutcome:
        if not self.base_url:
            raise ExternalServiceError(
                SEARCH_UNCONFIGURED, "没有配置检索服务地址（search_base_url）"
            )
        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "language": "all",
            "safesearch": "0",
            "pageno": "1",
        }
        if time_range:
            params["time_range"] = time_range
        started = time.perf_counter()
        try:
            response = httpx.get(
                self.base_url.rstrip("/") + "/search",
                params=params,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise ExternalServiceError(SEARCH_TIMEOUT, "检索请求超时") from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError(SEARCH_NETWORK_ERROR, "检索服务网络请求失败") from exc
        if response.status_code == 403:
            # 实例没有开启 json 格式时官方行为就是 403。
            raise ExternalServiceError(
                SEARCH_FORMAT_DISABLED, "检索实例未开启 JSON 输出（search.formats 需要包含 json）"
            )
        if response.status_code == 429:
            raise ExternalServiceError(SEARCH_RATE_LIMITED, "检索实例限流（HTTP 429）")
        if response.status_code >= 500:
            raise ExternalServiceError(
                SEARCH_SERVER_ERROR, f"检索实例返回 HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise ExternalServiceError(
                SEARCH_SERVER_ERROR, f"检索请求被拒绝（HTTP {response.status_code}）"
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        hits, unresponsive = _parse(response, limit)
        logger.info(
            "search_done query=%s hits=%d unresponsive=%d %dms",
            query[:40],
            len(hits),
            len(unresponsive),
            duration_ms,
        )
        return SearchOutcome(hits=tuple(hits), unresponsive_engines=tuple(unresponsive))


def _parse(response: httpx.Response, limit: int) -> tuple[list[SearchHit], list[str]]:
    """解析响应：命中按本地上限截断，未响应引擎单独返回。"""

    try:
        payload: object = response.json()
    except ValueError as exc:
        raise ExternalServiceError(SEARCH_INVALID_OUTPUT, "检索实例返回的不是 JSON") from exc
    if not isinstance(payload, dict):
        raise ExternalServiceError(SEARCH_INVALID_OUTPUT, "检索实例返回的 JSON 结构不符")
    body = cast(dict[str, object], payload)
    raw_results = body.get("results")
    if not isinstance(raw_results, list):
        raise ExternalServiceError(SEARCH_INVALID_OUTPUT, "检索结果缺少 results 字段")
    hits: list[SearchHit] = []
    for item in cast(list[object], raw_results):
        if not isinstance(item, dict):
            continue
        record = cast(dict[str, object], item)
        url = str(record.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            SearchHit(
                title=str(record.get("title") or "").strip(),
                url=url,
                snippet=str(record.get("content") or "").strip(),
                engine=str(record.get("engine") or ""),
                published_at=_published_at(record.get("publishedDate")),
            )
        )
        if len(hits) >= max(1, limit):
            break
    return hits, _unresponsive(body.get("unresponsive_engines"))


def _unresponsive(raw: object) -> list[str]:
    """未响应的引擎名单：官方返回 [[引擎, 原因], …]。"""

    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in cast(list[object], raw):
        if isinstance(item, list):
            # 官方形状是 [引擎, 原因]；取引擎名，原因只作日志线索。
            parts = cast(list[object], item)
            if parts:
                names.append(str(parts[0]))
        elif isinstance(item, str):
            names.append(item)
    return names


def _published_at(raw: object) -> int | None:
    """`publishedDate` → UTC 秒；缺失（含字面量 "None"）或解析失败返回 None。"""

    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return int(parsed.timestamp())
