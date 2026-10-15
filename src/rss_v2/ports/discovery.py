"""阶段 4 选题发现的端口：批次/候选仓储、写作项目仓储、发现输入读取。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rss_v2.domain import (
    DiscoveryBatch,
    DiscoveryCandidate,
    DiscoveryInputVersion,
    TaskLease,
    WritingProject,
)


class MessageScopeReader(Protocol):
    """按时间窗口与分类/来源读取发现输入（每个消息取最新版本）。

    与消息列表不同：这里不带关键词过滤，也不分页给界面用，而是给发现任务按确定
    顺序分批取数——没有加工结果的消息同样必须出现，否则短快讯永远进不了选题。
    """

    def scope_versions(
        self,
        since: int,
        until: int,
        source_id: str | None,
        category_id: str | None,
        limit: int,
        offset: int,
    ) -> list[DiscoveryInputVersion]:
        """取窗口内的消息版本，按 (发布时间回落后, 消息 id) 升序；`until` 不含。"""
        ...

    def count_scope_versions(
        self, since: int, until: int, source_id: str | None, category_id: str | None
    ) -> int:
        """窗口内的消息条数，用于上限校验与批次计数。"""
        ...


class DiscoveryRepository(Protocol):
    """发现批次与候选的持久化。"""

    def create(self, batch: DiscoveryBatch) -> DiscoveryBatch:
        """写入批次（初始为 queued）。"""
        ...

    def get(self, batch_id: str) -> DiscoveryBatch | None:
        """按 id 取批次；不存在返回 None。"""
        ...

    def get_by_idempotency(self, key: str) -> DiscoveryBatch | None:
        """按幂等键取批次：重复请求与重试都靠它复用同一个批次。"""
        ...

    def list(
        self, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[DiscoveryBatch], int]:
        """批次列表与总数，按创建时间倒序。"""
        ...

    def update(self, batch: DiscoveryBatch, lease: TaskLease | None = None) -> DiscoveryBatch:
        """整体更新批次的可变字段（状态、计数、错误、task_id）。

        传入租约时在同一事务里校验它：过期 worker 的晚到结果不能覆盖当前批次。
        """
        ...

    def replace_candidates(
        self,
        batch_id: str,
        candidates: Sequence[DiscoveryCandidate],
        lease: TaskLease | None = None,
    ) -> int:
        """用本次结果替换该批次的候选，返回写入条数。

        重试（同一任务再次执行）不允许把候选叠成两份：先按批次清掉旧候选，
        并顺带清理上次尝试留下、且没有被写作项目引用的自动线索。
        """
        ...

    def candidates(self, batch_id: str) -> list[DiscoveryCandidate]:
        """批次下的候选，按 rank 升序。"""
        ...

    def candidate(self, candidate_id: str) -> DiscoveryCandidate | None:
        """按 id 取候选；不存在返回 None。"""
        ...

    def delete(self, batch_id: str) -> int:
        """删除批次（候选级联）及其自动线索，返回删除行数。"""
        ...


class WritingProjectRepository(Protocol):
    """写作项目的持久化。"""

    def create(self, project: WritingProject) -> WritingProject:
        """写入项目；同一候选重复选择时返回已存在的项目（幂等）。"""
        ...

    def get(self, project_id: str) -> WritingProject | None:
        """按 id 取项目；不存在返回 None。"""
        ...

    def get_by_candidate(self, candidate_id: str) -> WritingProject | None:
        """按候选取项目：一个候选只对应一个写作项目。"""
        ...

    def list(
        self, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[WritingProject], int]:
        """项目列表与总数，按最近更新时间倒序。"""
        ...

    def set_status(self, project_id: str, status: str, timestamp: int) -> int:
        """推进项目状态，返回更新行数（只允许向前推进由用例层保证）。"""
        ...

    def set_materials_summary(
        self,
        project_id: str,
        status: str,
        summary_json: str,
        timestamp: int,
        lease: TaskLease | None = None,
    ) -> int:
        """写入资料准备的结果摘要并推进状态，返回更新行数。

        摘要（检索式、各状态条数、缺口说明）存在项目行上：资料行是权威数据，
        摘要是"上一轮跑到哪、缺什么"的展示口径，界面不必自己拼。
        """
        ...
