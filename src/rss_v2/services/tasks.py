"""后台任务查询用例。"""

from dataclasses import dataclass

from rss_v2.domain import DomainError, Task, TaskStatus, TaskType
from rss_v2.ports import TaskRepository


@dataclass(slots=True)
class TaskService:
    """任务状态读取。"""

    tasks: TaskRepository

    def get(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise DomainError("task_not_found", "任务不存在")
        return task

    def list(self, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Task]:
        """查询任务列表。"""
        return self.tasks.list(status, limit, offset)

    def for_version(self, version_id: str, task_type: TaskType) -> Task | None:
        """查询版本最近的一类任务；翻译与内容加工分别展示状态。"""
        return self.tasks.latest_for_version(version_id, task_type)

    def retry(self, task_id: str) -> Task:
        """明确重试失败任务。"""
        task = self.get(task_id)
        if task.status != TaskStatus.FAILED:
            raise DomainError("task_not_failed", "只有失败任务可以手动重试")
        return self.tasks.retry(task_id)
