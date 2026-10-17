"""资料准备用例：为写作项目读取原文与联网补充，落成可追溯的资料快照。

一次运行（一个任务）：

1. **生成检索式**：把选题与成员报道交给模型，产出一至三条检索式（中文、英文、官方站点），
   用途一并记下来，便于人工复核"为什么搜这些"。
2. **读 RSS 原文**：成员消息版本即订阅到的原文；正文为空的短消息（例如电报/推特转发）
   再按它的链接回读一次页面。
3. **联网补充**：按检索式搜索，命中只是候选——**读取成功才算资料**；官方站点检索式
   优先读取，便于补齐一手资料。
4. **去重与快照**：同一项目内按规范化 URL 与内容指纹去重，重复只计数；每条资料保存
   URL、发布者、原始发布时间、获取时间、正文与指纹，RSS 资料固定消息版本。
5. **预算与缺口**：读取条数、字节与检索次数都有上限；超限的候选落成"未读取"占位行
   （error_code=budget_exhausted），摘要里写明缺口，不静默丢材料。

检索失败（未配置、超时、限流、格式未开）按现有阶梯抛出并冷却重试；**单份资料读取失败
不会失败整个任务**：它落成 failed 行，重跑时按地址跳过已读过的，已获资料不丢。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rss_v2.domain import (
    DomainError,
    Material,
    MaterialLimits,
    MaterialSummary,
    ProjectStatus,
    Task,
    TaskStatus,
    TaskType,
    lease_of,
)
from rss_v2.domain.values import new_id, now
from rss_v2.ports import (
    LeadRepository,
    MaterialRepository,
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.llm_failover import LLMFailover
from rss_v2.services.materials_runner import MaterialRunner
from rss_v2.services.materials_support import snapshot_hash, summary_json
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 去重时忽略的跟踪参数：同一篇文章带不同 utm 参数不该读两遍。
_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "spm", "from")

#: 摘要里最多保留几条缺口说明。
MAX_NOTES = 6

#: 一次运行最多考虑多少条检索候选：再多也只是排在预算之外，徒增占位行。
MAX_CANDIDATES_PER_RUN = 40

#: 预算耗尽时为多少条候选留"未读取"占位行；其余只计入摘要的数字。
MAX_SKIPPED_ROWS = 8


@dataclass(slots=True)
class MaterialService:
    """资料准备的创建、执行与查询。"""

    materials: MaterialRepository  # 资料快照
    projects: WritingProjectRepository  # 写作项目（状态与摘要）
    leads: LeadRepository  # 选题成员（消息版本）
    tasks: TaskRepository  # 资料准备任务
    prompts: PromptResolver  # 检索式提示词
    failover: LLMFailover  # 入队时的模型候选
    runner: MaterialRunner  # 一次运行的执行器（检索、读取、去重、预算）
    limits: MaterialLimits = MaterialLimits()

    # ---------------------------------------------------------------- 入队

    def create(self, project_id: str, force: bool = False) -> tuple[Task, bool]:
        """创建（或复用）资料准备任务。

        默认对"同一份订阅输入"去重：重复点击不会重复付费。`force=True` 是用户主动的
        "重新获取资料"：新起一个任务重新检索与读取（已读地址与内容指纹仍会跳过，
        因此不会产生重复资料），用于来源更新或上次有资料没读到的情况。
        """

        project = self._require_project(project_id)
        members = self.leads.members(project.lead_id)
        if not members:
            raise DomainError("project_members_empty", "这个选题的成员消息都已不存在，无法准备资料")
        snapshot = snapshot_hash(members)
        key = f"materials:{project.id}:{snapshot}"
        existing = self.tasks.get_by_idempotency(key)
        if (
            not force
            and existing is not None
            and existing.status
            in {
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.SUCCEEDED.value,
            }
        ):
            return existing, True
        snapshot_values = self._snapshot(existing)
        if existing is not None:
            key = f"{key}:{'run' if force else 'retry'}:{now()}"
        task = self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.PREPARE_MATERIALS,
                idempotency_key=key,
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=None,
                output_version_id=None,
                payload={
                    "project_id": project.id,
                    "versions": [member.version_id for member in members],
                    **snapshot_values,
                },
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
        )
        return task, False

    # ---------------------------------------------------------------- 执行

    def run(self, task: Task) -> MaterialSummary:
        """执行资料准备：先确认项目与任务租约，再交给执行器读资料并写回摘要。

        执行器负责"一次运行怎么跑"（检索式、RSS 原文、联网补充、去重与预算），
        这一层只负责项目校验、预算注入与结果落库，便于单独验证两侧行为。
        """

        lease = lease_of(task)
        project = self._require_project(str(task.payload.get("project_id", "")))
        summary = self.runner.run(task, project, self.limits, lease)
        self.projects.set_materials_summary(
            project.id,
            ProjectStatus.MATERIALS_READY.value,
            summary_json(summary, task.id),
            summary.finished_at,
            lease,
        )
        self.tasks.complete(task.id, lease_token=task.lease_token)
        logger.info(
            "materials_ready project=%s read=%d summary_only=%d failed=%d duplicates=%d skipped=%d",
            project.id,
            summary.read,
            summary.summary_only,
            summary.failed,
            summary.duplicates,
            summary.skipped_by_budget,
        )
        return summary

    # ---------------------------------------------------------------- 查询

    def list(self, project_id: str) -> list[Material]:
        """项目的资料列表（不含正文，正文走详情）。"""

        self._require_project(project_id)
        return self.materials.list(project_id)

    def counts(self, project_id: str) -> dict[str, int]:
        """项目资料条数（按状态）。"""

        return self.materials.count(project_id)

    def summary_view(self, project_id: str) -> tuple[str, str | None]:
        """项目的资料摘要（JSON 原文）与最近一次资料任务的状态。

        界面据此显示"排队/运行/失败/完成"：任务状态是权威的在途状态，摘要记录上一次结果。
        """

        project = self._require_project(project_id)
        # 任务状态取**最近一次 prepare_materials 任务**：摘要里的 task_id 只在成功时写入，
        # 用它会让"排队中/失败"的任务在界面上完全看不见（与资料整理同一口径）。
        status: str | None = None
        for task in self.tasks.list(task_type=TaskType.PREPARE_MATERIALS.value, limit=20):
            if str(task.payload.get("project_id", "")) == project.id:
                status = task.status
                break
        return project.materials_summary_json or "{}", status

    def detail(self, project_id: str, material_id: str) -> Material:
        """一条资料的完整快照（含正文）。"""

        self._require_project(project_id)
        material = self.materials.get(material_id)
        if material is None or material.project_id != project_id:
            raise DomainError("material_not_found", "资料不存在")
        return material

    # ---------------------------------------------------------------- 内部

    def _snapshot(self, existing: Task | None) -> dict[str, str]:
        """任务里的模型与提示词快照：重试沿用上次，首次取当前生效配置。"""

        if existing is not None:
            stored = {
                "provider_id": str(existing.payload.get("provider_id", "")),
                "model": str(existing.payload.get("model", "")),
                "prompt_id": str(existing.payload.get("prompt_id", "")),
                "prompt_version": str(existing.payload.get("prompt_version", "")),
            }
            if stored["provider_id"] and stored["model"] and stored["prompt_version"]:
                return stored
        prompt = self.prompts.active(TaskType.PREPARE_MATERIALS.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的资料检索式提示词")
        provider, model = self.failover.require_available()
        return {
            "provider_id": provider.id,
            "model": model.model,
            "prompt_id": prompt.id,
            "prompt_version": prompt.version_string,
        }

    def _require_project(self, project_id: str):
        project = self.projects.get(project_id)
        if project is None:
            raise DomainError("project_not_found", "写作项目不存在")
        return project
