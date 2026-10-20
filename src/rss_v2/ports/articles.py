"""阶段 4 全文（P5）的端口：文章版本的持久化。"""

from __future__ import annotations

from typing import Protocol

from rss_v2.domain import ArticleVersion, TaskLease


class ArticleRepository(Protocol):
    """全文版本的持久化。

    版本只追加：模型生成与 P6a 的人工保存都写新行，历史行不被改写。
    正文、引用快照与来源列表都随版本一起落库，读取时不依赖当时的资料包。
    """

    def add(self, version: ArticleVersion, lease: TaskLease | None = None) -> None:
        """写入一个全文版本；同一事务里校验租约。"""
        ...

    def get(self, version_id: str) -> ArticleVersion | None:
        """按 id 取版本；不存在返回 None。"""
        ...

    def versions(self, project_id: str, limit: int = 20) -> list[ArticleVersion]:
        """项目内的版本列表（新到旧）。"""
        ...

    def latest_number(self, project_id: str) -> int:
        """项目内最大的版本序号；没有版本时为 0。"""
        ...

    def saved(self, project_id: str) -> ArticleVersion | None:
        """最新的人工保存版本（P6a 之后才会出现）；没有时返回 None。"""
        ...

    def count(self, project_id: str) -> int:
        """项目内的版本条数（项目详情的产物计数用）。"""
        ...

    def delete(self, version_id: str) -> int:
        """删除一个版本；仅测试与失败回滚使用。"""
        ...
