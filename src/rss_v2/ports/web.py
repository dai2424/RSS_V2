"""阶段 4 的外部读取端口：联网检索与网页读取。

两个端口都只返回数据，不做落库、不做去重、不做预算判断——那些属于用例层。
适配器在 `adapters/web/`，只在组合根装配；服务层不直接调用 httpx。
"""

from __future__ import annotations

from typing import Protocol

from rss_v2.domain import ArticleRead, SearchOutcome


class SearchClient(Protocol):
    """联网检索端口（当前实现：SearXNG 兼容 JSON 端点）。"""

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> SearchOutcome:
        """按检索式取命中；返回命中与"未响应引擎"。

        未配置服务地址、超时、限流、格式未开等失败抛出 `ExternalServiceError`
        （按现有阶梯冷却重试）；"没有命中"是正常结果，返回空列表。
        """
        ...


class ArticleReader(Protocol):
    """原文读取端口：抓取页面并抽取标题、发布者、发布时间与正文。"""

    def read(self, url: str) -> ArticleRead:
        """读取一份原文；失败（超时、404、体积超限、无法解析、私网地址）也返回结果。

        单份资料失败不该让整次资料准备失败：调用方会把结果落成资料行并继续读下一份。
        """
        ...
