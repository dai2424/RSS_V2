"""任务租约校验：写入结果的适配器在同一事务里确认自己仍是领取者。

翻译、加工、采集与合并建议各自的写入点都要这道校验，抽成一份：四份拷贝里漏改一处，
过期 worker 就能继续覆盖结果。
"""

from __future__ import annotations

import sqlite3

from rss_v2.domain import DomainError, TaskLease


def ensure_lease(connection: sqlite3.Connection, lease: TaskLease | None) -> None:
    """确认任务仍由该领取者持有，否则抛 `task_lease_lost`。

    必须在写入所在的事务里调用：分开两次写会出现"校验通过、写入前租约刚被回收"的窗口。
    lease 为 None 表示调用方没有任务上下文（界面触发的同步路径），不做校验。
    """

    if lease is None:
        return
    owned = connection.execute(
        "SELECT 1 FROM tasks WHERE id=? AND status='running' AND lease_token=?",
        (lease.task_id, lease.token),
    ).fetchone()
    if owned is None:
        raise DomainError("task_lease_lost", "任务租约已被回收，当前结果不再写入")
