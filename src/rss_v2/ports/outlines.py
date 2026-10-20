"""阶段 4 提纲（P4）的端口：提纲版本的持久化。"""

from __future__ import annotations

from typing import Protocol

from rss_v2.domain import OutlineVersion, TaskLease


class OutlineRepository(Protocol):
    """提纲版本的持久化。

    版本只追加：模型生成与人工保存都写新行，历史行不被改写（确认只改状态列）。
    确认是"至多一个"的约束，由 `confirm` 在同一事务里把旧的已确认版本转成
    superseded。
    """

    def add(self, version: OutlineVersion, lease: TaskLease | None = None) -> None:
        """写入一个提纲版本；同一事务里校验租约。"""
        ...

    def get(self, version_id: str) -> OutlineVersion | None:
        """按 id 取版本；不存在返回 None。"""
        ...

    def versions(self, project_id: str, limit: int = 20) -> list[OutlineVersion]:
        """项目内的版本列表（新到旧）。"""
        ...

    def latest(self, project_id: str) -> OutlineVersion | None:
        """项目内序号最大的版本；一个版本都没有时返回 None。"""
        ...

    def confirmed(self, project_id: str) -> OutlineVersion | None:
        """项目当前已确认的版本；没有确认过时返回 None（全文生成的前置）。"""
        ...

    def latest_number(self, project_id: str) -> int:
        """项目内最大的版本序号；没有版本时为 0（用于判定"是不是最新"）。"""
        ...

    def count(self, project_id: str) -> int:
        """项目内的版本条数（项目详情的产物计数用）。"""
        ...

    def confirm(
        self, project_id: str, version_id: str, timestamp: int, lease: TaskLease | None = None
    ) -> int:
        """确认一个版本，返回更新行数。

        只在目标版本仍是项目内序号最大的版本、且状态不是 superseded 时生效；
        同一事务里把该项目原来已确认的版本转为 superseded。返回 0 表示版本已被
        更新的版本取代，调用方据此报"不是最新版本"。
        """
        ...

    def delete(self, version_id: str) -> int:
        """删除一个版本；仅测试与失败回滚使用。"""
        ...
