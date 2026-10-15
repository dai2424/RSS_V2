"""阶段 4 资料存储端口：写作项目的资料快照。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rss_v2.domain import Material, TaskLease


class MaterialRepository(Protocol):
    """资料行的持久化；同一项目内按内容指纹去重。"""

    def add(self, material: Material, lease: TaskLease | None = None) -> Material | None:
        """写入一条资料；内容指纹已存在时返回 None（重复读取不产生第二行）。

        传入租约时在同一事务里校验：过期 worker 的晚到资料不能写进当前结果。
        """
        ...

    def list(self, project_id: str) -> list[Material]:
        """项目的资料列表，按获取时间与创建顺序排列。"""
        ...

    def get(self, material_id: str) -> Material | None:
        """按 id 取资料；不存在返回 None。"""
        ...

    def by_url(self, project_id: str, url: str) -> Material | None:
        """按地址（原始或最终）取资料：重跑时跳过已读过的地址。"""
        ...

    def clear_budget_skips(self, project_id: str, lease: TaskLease | None = None) -> int:
        """清掉上一轮"预算耗尽未读取"的占位行，返回删除条数。

        这些行不是真实结论，只是上一轮的缺口记录；重跑时应当重新评估，
        否则会一直显示旧的"未读取"。
        """
        ...

    def count(self, project_id: str) -> dict[str, int]:
        """按状态统计，供项目摘要与界面展示。"""
        ...

    def delete_for_project(self, project_id: str) -> int:
        """删除项目的全部资料（项目删除/重置时使用），返回删除条数。"""
        ...

    def add_all(self, materials: Sequence[Material], lease: TaskLease | None = None) -> int:
        """批量写入（逐条去重），返回实际新增条数。"""
        ...
