"""worker 续租线程，只负责当前任务的心跳。"""

import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager

from rss_v2.domain import Task
from rss_v2.ports import TaskRepository


@contextmanager
def keep_lease(
    tasks: TaskRepository, task: Task, lease_seconds: int
) -> Generator[None, None, None]:
    """网络 IO 期间续租；离开时停止线程，API 不创建后台任务线程。"""
    stopped = threading.Event()

    def renew() -> None:
        while not stopped.wait(max(1, lease_seconds // 3)):
            try:
                if not tasks.renew(
                    task.id, task.lease_token or "", int(time.time()), lease_seconds
                ):
                    logging.getLogger(__name__).error("task_lease_lost task_id=%s", task.id)
                    break
            except Exception:
                logging.getLogger(__name__).error("task_lease_error task_id=%s", task.id)

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)
