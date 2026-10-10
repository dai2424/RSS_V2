"""分页响应的公共模型；四个列表接口共用一套 items + total 形状。"""

from __future__ import annotations

from pydantic import BaseModel


class Page[T](BaseModel):
    """分页结果。

    total 是当前过滤条件下的总数，与 items 的分页窗口无关：两处必须来自同一套
    过滤条件，否则页面上会出现「总数比翻得到的条数少」这类对不上的数字。
    """

    items: list[T]
    total: int
