"""独立 worker 的任务循环。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rss_v2.bootstrap import Container
from rss_v2.domain import DomainError, ExternalServiceError, TaskType


@dataclass(slots=True)
class Worker:
    """从 SQLite 任务表领取并执行任务。"""

    container: Container

    def run_once(self) -> bool:
        """执行一个任务，返回是否领取到任务。"""

        tasks = self.container.tasks
        tasks.reclaim_expired(int(time.time()))
        task = tasks.claim_next(
            [TaskType.COLLECT_SOURCE.value, TaskType.TRANSLATE_MESSAGE.value],
            int(time.time()),
            self.container.settings.worker_lease_seconds,
        )
        if task is None:
            return False
        try:
            if task.task_type == TaskType.COLLECT_SOURCE:
                source_id = str(task.payload.get("source_id", ""))
                run_id = str(task.payload.get("run_id", "")) or None
                self.container.collection_service.collect(source_id, run_id)
                tasks.complete(task.id)
            elif task.task_type == TaskType.TRANSLATE_MESSAGE:
                self.container.translation_service.run(task)
                tasks.complete(task.id)
            else:
                tasks.fail(task.id, "task_type_unsupported", "不支持的任务类型", False)
        except ExternalServiceError as exc:
            tasks.fail(task.id, exc.code, exc.message, task.attempts < 3)
        except DomainError as exc:
            retryable = exc.code in {"llm_invalid_output", "version_changed"} and task.attempts < 2
            tasks.fail(task.id, exc.code, exc.message, retryable)
        except Exception as exc:
            tasks.fail(task.id, "worker_error", str(exc), task.attempts < 3)
        return True

    def run_forever(self) -> None:
        """持续轮询任务表。"""

        while True:
            if not self.run_once():
                time.sleep(self.container.settings.worker_poll_seconds)
