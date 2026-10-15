"""阶段 4 资料整理（P3b）的端口：资料包版本、事实、证据与待办。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rss_v2.domain import DigestCounts, DigestRun, Evidence, Fact, Note, TaskLease


class FactRepository(Protocol):
    """事实、证据、待办与资料包版本的持久化。

    写入按版本（run）追加：重跑产生新版本，历史版本的依据不被改写；
    待办可以人工标记已处理，重跑保留已处理的记录。
    """

    def create_run(self, run_id: str, project_id: str, materials_hash: str, timestamp: int) -> None:
        """开一次整理版本（先落 run，便于失败时也能追溯输入）。"""
        ...

    def finish_run(
        self,
        run_id: str,
        task_id: str,
        counts: DigestCounts,
        notes: Sequence[str],
        lease: TaskLease | None = None,
    ) -> int:
        """写入版本计数并收尾；同一事务里校验租约。"""
        ...

    def add_evidence(self, evidence: Evidence, lease: TaskLease | None = None) -> None:
        """写入一条支持证据。"""
        ...

    def add_fact(
        self, fact: Fact, evidence_ids: Sequence[str], lease: TaskLease | None = None
    ) -> None:
        """写入一条事实及其证据关系。"""
        ...

    def add_note(self, note: Note, lease: TaskLease | None = None) -> None:
        """写入一条缺口/分歧待办。"""
        ...

    def latest_run(self, project_id: str) -> str | None:
        """最近一次整理版本 id；没有整理过时返回 None。"""
        ...

    def runs(self, project_id: str, limit: int = 10) -> list[str]:
        """整理版本列表（新到旧）。"""
        ...

    def get_run(self, run_id: str) -> DigestRun | None:
        """按 id 取版本记录（含计数）；不存在返回 None。"""
        ...

    def facts(self, run_id: str) -> list[Fact]:
        """某个版本的事实（按写入顺序），含证据。"""
        ...

    def notes(self, run_id: str) -> list[Note]:
        """某个版本的待办（含已处理）。"""
        ...

    def get_note(self, note_id: str) -> Note | None:
        """按 id 取待办。"""
        ...

    def resolve_note(self, note_id: str, timestamp: int) -> int:
        """标记待办已处理，返回更新行数。"""
        ...

    def unresolved_blocking(self, project_id: str) -> int:
        """该项目当前未处理的阻塞缺口数（所有版本里仍未处理的）。"""
        ...

    def delete_run(self, run_id: str) -> int:
        """删除一个版本（含事实、证据与待办）；仅测试与回滚使用。"""
        ...
