"""外部服务端口。"""

from __future__ import annotations

from typing import Protocol

from rss_v2.domain import FeedSnapshot


class FeedClient(Protocol):
    """RSS 获取和解析端口。"""

    def fetch(self, url: str, timeout_seconds: float) -> FeedSnapshot: ...
