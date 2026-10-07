"""后台任务查询用例。"""

from dataclasses import dataclass

from rss_v2.domain import DomainError, Task
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
