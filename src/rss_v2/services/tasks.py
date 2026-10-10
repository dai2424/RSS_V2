"""后台任务查询用例。

列表除了任务本体，还解析"目标"（哪条消息、哪个来源）：任务快照里只存 UUID，
界面要说清这条任务在干什么。目标按页内任务逐条点查，本地库一页最多 100 条，
比在读模型里复制一份任务 SQL 更简单也更难出错。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rss_v2.domain import DomainError, Task, TaskDeletion, TaskStatus, TaskType, TaskView
from rss_v2.ports import MessageRepository, SourceRepository, TaskRepository

#: 无法解析目标时回落的标识长度，够在一眼内区分不同目标。
SHORT_ID_LENGTH = 8


@dataclass(slots=True)
class TaskService:
    """任务状态读取与目标说明。"""

    tasks: TaskRepository
    messages: MessageRepository
    sources: SourceRepository

    def get(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise DomainError("task_not_found", "任务不存在")
        return task

    def list(self, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Task]:
        """查询任务列表。"""
        return self.tasks.list(status, limit, offset)

    def list_views(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> Sequence[TaskView]:
        """任务列表加目标说明，供界面直接用。"""

        return [self.view(task) for task in self.tasks.list(status, limit, offset)]

    def count(self, status: str | None = None) -> int:
        """与任务列表相同筛选条件下的总数，供列表分页使用。"""

        return self.tasks.count(status)

    def view(self, task: Task) -> TaskView:
        """单个任务的目标说明。"""

        kind, target_id = self._target(task)
        if not target_id:
            return TaskView(task, "unknown", "", "")
        if kind == "message":
            version = self.messages.latest_version(target_id)
            title = version.title.strip() if version else ""
            # 标题为空（例如未采集到标题）时回落为短标识，避免整列空白。
            return TaskView(task, kind, target_id, title or self._short(target_id))
        source = self.sources.get(target_id)
        return TaskView(task, kind, target_id, source.name if source else self._short(target_id))

    def for_version(self, version_id: str, task_type: TaskType) -> Task | None:
        """查询版本最近的一类任务；翻译与内容加工分别展示状态。"""
        return self.tasks.latest_for_version(version_id, task_type)

    def delete(self, task_id: str) -> TaskDeletion:
        """删除已结束的任务。

        排队与运行中的任务不能删：运行中的任务被删会让 worker 在回写时崩，排队中的
        collect_source 任务被删会让采集运行永远停在 running。仓储层用状态条件再挡一次。
        """

        task = self.get(task_id)
        if task.status not in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            raise DomainError("task_not_finished", "排队中或运行中的任务不能删除")
        return TaskDeletion(candidates=1, deleted=self.tasks.delete_finished([task_id]))

    def clear_failed(self, dry_run: bool) -> TaskDeletion:
        """清理全部失败任务；先预览条数再执行。"""

        candidates = self.tasks.count(TaskStatus.FAILED)
        if dry_run:
            return TaskDeletion(candidates=candidates, deleted=0)
        return TaskDeletion(candidates=candidates, deleted=self.tasks.delete_failed())

    def retry(self, task_id: str) -> Task:
        """明确重试失败任务。"""
        task = self.get(task_id)
        if task.status != TaskStatus.FAILED:
            raise DomainError("task_not_failed", "只有失败任务可以手动重试")
        return self.tasks.retry(task_id)

    @staticmethod
    def _target(task: Task) -> tuple[str, str]:
        """任务目标：翻译与内容加工作用于消息，采集作用于来源。"""

        if task.task_type == TaskType.COLLECT_SOURCE.value:
            return "source", str(task.payload.get("source_id") or "")
        return "message", str(task.payload.get("message_id") or "")

    @staticmethod
    def _short(target_id: str) -> str:
        return target_id[:SHORT_ID_LENGTH]
