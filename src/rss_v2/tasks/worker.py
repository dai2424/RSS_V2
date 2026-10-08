"""独立 worker 的任务循环。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from rss_v2.bootstrap import Container
from rss_v2.domain import LLM_TASK_TYPES, DomainError, ExternalServiceError, Task, TaskType
from rss_v2.tasks.lease import keep_lease


@dataclass(slots=True)
class Worker:
    """从 SQLite 任务表领取并执行任务。"""

    container: Container

    def run_once(self) -> bool:
        """执行一个任务，返回是否领取到任务。"""

        tasks = self.container.tasks
        tasks.reclaim_expired(int(time.time()))
        task = tasks.claim_next(
            [task_type.value for task_type in TaskType],
            int(time.time()),
            self.container.settings.worker_lease_seconds,
        )
        if task is None:
            return False
        logging.getLogger(__name__).info("task_started id=%s type=%s", task.id, task.task_type)
        try:
            with keep_lease(tasks, task, self.container.settings.worker_lease_seconds):
                self.execute(task)
            if task.task_type not in LLM_TASK_TYPES:
                tasks.complete(task.id, lease_token=task.lease_token)
        except ExternalServiceError as exc:
            delay = (
                max(60, self.container.settings.llm_retry_cooldown_seconds)
                if task.task_type in LLM_TASK_TYPES
                else 60
            )
            tasks.fail(task.id, exc.code, exc.message, task.attempts < 3, task.lease_token, delay)
        except DomainError as exc:
            tasks.fail(task.id, exc.code, exc.message, False, task.lease_token)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "task_error id=%s class=%s", task.id, type(exc).__name__
            )
            tasks.fail(
                task.id,
                "worker_error",
                "任务执行发生内部错误，请查看运行日志",
                task.attempts < 3,
                task.lease_token,
            )
        if task.task_type == TaskType.COLLECT_SOURCE and task.payload.get("run_id"):
            self.container.collection_service.refresh_run(str(task.payload["run_id"]))
        logging.getLogger(__name__).info("task_finished id=%s", task.id)
        return True

    def execute(self, task: Task) -> None:
        """分发当前任务，不包含领取逻辑。"""

        if task.task_type == TaskType.COLLECT_SOURCE:
            self.container.collection_service.collect(
                str(task.payload.get("source_id", "")),
                str(task.payload.get("run_id", "")) or None,
            )
        elif task.task_type == TaskType.TRANSLATE_MESSAGE:
            translation = self.container.translation_service.run(task)
            self.container.tasks.complete(task.id, translation.id, task.lease_token)
        elif task.task_type == TaskType.ENRICH_MESSAGE:
            enrichment = self.container.enrichment_service.run(task)
            self.container.tasks.complete(task.id, enrichment.id, task.lease_token)
        else:
            raise DomainError("task_type_unsupported", "不支持的任务类型")

    def run_forever(self) -> None:
        """持续轮询任务表。"""

        while True:
            if not self.run_once():
                time.sleep(self.container.settings.worker_poll_seconds)
