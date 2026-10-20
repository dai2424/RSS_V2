"""提纲用例：生成、人工修改保存与确认。

一次运行（一个任务）：

1. **输入**：项目最新一次资料整理版本（`digest_run_id` 钉住它）里的事实与待办，
   加上选题标题与线索备注。输入指纹沿用契约口径 `material_snapshot_hash`
   （即那个版本的 `digest_runs.materials_hash`）。
2. **生成**：模型按 `OutlineDraft` 输出标题、切入点与分节；分节里的事实与引文 id
   由服务端逐条核对（越界、指向缺引文事实的引用丢弃并计数）。
3. **版本**：每次生成追加一个 `draft` 版本，历史版本不改写。同一资料包重复点击
   复用同一任务；换过资料包（新的 `digest_run_id`）或用户主动重生成则新起版本。
4. **确认**：只允许确认项目内序号最大的版本；确认时旧的已确认版本转为
   `superseded`，项目状态推进到 `outline_confirmed`。

**未解决的阻塞缺口不拦生成与确认**，只随响应返回（`has_blocking` / `blocking_count`）
由界面显著标记：是否拒绝生成全文留给 P5 判断，提纲阶段先把问题摆出来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rss_v2.domain import (
    ConflictError,
    DomainError,
    Fact,
    OutlineDraft,
    OutlineLimits,
    OutlineSource,
    OutlineStatus,
    OutlineVersion,
    ProjectStatus,
    Prompt,
    Task,
    TaskStatus,
    TaskType,
    WritingProject,
    lease_of,
)
from rss_v2.domain.outline_rules import (
    fact_index,
    facts_text,
    normalize_outline,
    normalize_title,
    notes_text,
)
from rss_v2.domain.values import new_id, now
from rss_v2.ports import (
    FactRepository,
    LeadRepository,
    OutlineRepository,
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.llm_failover import LLMFailover
from rss_v2.services.outline_runner import OutlineRunner
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 列表与详情最多返回几个版本（历史版本只用于回溯，不需要全量）。
MAX_VERSIONS = 20


@dataclass(frozen=True, slots=True)
class OutlineView:
    """提纲列表视图：版本 + 项目级的当前状态。"""

    versions: tuple[OutlineVersion, ...]
    latest_version_id: str | None
    confirmed_version_id: str | None
    blocking_count: int
    task_status: str | None

    @property
    def has_blocking(self) -> bool:
        """项目里是否还有未处理的阻塞缺口（只标记，不拦生成与确认）。"""

        return self.blocking_count > 0

    @property
    def has_unconfirmed_changes(self) -> bool:
        """已有确认版本，但最新版本不是它（确认后又改过或重新生成过）。"""

        return (
            self.confirmed_version_id is not None
            and self.latest_version_id != self.confirmed_version_id
        )


@dataclass(slots=True)
class OutlineService:
    """提纲的创建、执行、查询、人工保存与确认。"""

    outlines: OutlineRepository  # 提纲版本
    runs: FactRepository  # 资料包版本、事实与待办（提纲的依据）
    projects: WritingProjectRepository  # 项目状态
    leads: LeadRepository  # 选题备注（作为切入点提示）
    tasks: TaskRepository  # 生成任务
    prompts: PromptResolver  # 生效提示词
    failover: LLMFailover  # 取可用 Provider×模型（任务快照）
    runner: OutlineRunner  # 一次生成的执行器
    limits: OutlineLimits = OutlineLimits()

    # ---------------------------------------------------------------- 入队

    def create(self, project_id: str, force: bool = False) -> tuple[Task, bool]:
        """创建（或复用）提纲生成任务。

        默认同一资料包不重复付费；`force=True` 是用户主动的"重新生成"，用于换过模型、
        提示词或想重写一版的情况——仍按版本追加，不改写历史提纲。
        """

        project = self._require_project(project_id)
        digest_run_id = self.runs.latest_run(project_id)
        if digest_run_id is None:
            raise DomainError(
                "project_status_invalid", "这个项目还没有资料整理结果，请先完成资料整理"
            )
        run = self.runs.get_run(digest_run_id)
        if run is None:
            raise DomainError("digest_not_found", "资料整理版本不存在，请重新整理资料")
        key = f"outline:{project.id}:{run.materials_hash}"
        existing = self.tasks.get_by_idempotency(key)
        if not force and existing is not None and self._reusable(existing, digest_run_id):
            return existing, True
        snapshot = self._snapshot(existing)
        if existing is not None:
            # 同一资料包但换了整理版本、或用户主动重生成：另起一个任务与版本。
            key = f"{key}:{'run' if force else 'retry'}:{now()}"
        task = self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.GENERATE_OUTLINE,
                idempotency_key=key,
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=None,
                output_version_id=None,
                payload={
                    "project_id": project.id,
                    "digest_run_id": digest_run_id,
                    "material_snapshot_hash": run.materials_hash,
                    **snapshot,
                },
                error_code=None,
                error_message=None,
                created_at=now(),
                updated_at=now(),
            )
        )
        return task, False

    # ---------------------------------------------------------------- 执行

    def run(self, task: Task) -> OutlineVersion:
        """执行生成：渲染依据 → 调用模型 → 校验引用 → 落库版本并推进项目状态。

        **只有产出有效提纲的尝试才写版本行**：模型输出不合法（不足 2 节）或调用失败
        都不留"空提纲"，否则界面会把一次失败显示成"生成完了但提纲是空的"。失败原因
        由任务行记录（任务列表与项目页都能看到错误分类与尝试次数）。
        """

        project = self._require_project(str(task.payload.get("project_id", "")))
        lease = lease_of(task)
        digest_run_id = str(task.payload.get("digest_run_id", ""))
        run = self.runs.get_run(digest_run_id)
        if run is None or run.project_id != project.id:
            raise DomainError("digest_not_found", "资料整理版本已失效，请重新整理资料")
        facts = self.runs.facts(run.id)
        if not facts:
            raise DomainError("outline_no_facts", "这次资料整理没有产出可用事实，无法生成提纲")
        notes = self.runs.notes(run.id)
        fact_status, evidence_ids = fact_index(facts)
        draft = self.runner.run(
            task,
            title=project.title,
            summary=self._lead_note(project.lead_id),
            facts=facts_text(
                facts, limit=self.limits.max_input_chars, fact_chars=self.limits.fact_chars
            ),
            notes=notes_text(notes, limit=self.limits.max_input_chars),
        )
        sections, counters = normalize_outline(
            draft,
            fact_status=fact_status,
            evidence_ids=evidence_ids,
            max_sections=self.limits.max_sections,
            max_points=self.limits.max_points,
        )
        title, angle = normalize_title(draft)
        timestamp = now()
        version = OutlineVersion(
            id=new_id(),
            project_id=project.id,
            version_number=self.outlines.latest_number(project.id) + 1,
            status=OutlineStatus.DRAFT.value,
            source=OutlineSource.MODEL.value,
            title=title or project.title,
            angle=angle,
            sections=sections,
            material_snapshot_hash=run.materials_hash,
            digest_run_id=run.id,
            prompt_version=str(task.payload.get("prompt_version", "")) or None,
            model=str(task.payload.get("model", "")) or None,
            task_id=task.id,
            based_on_version=None,
            created_at=timestamp,
            counters=counters,
        )
        self.outlines.add(version, lease)
        self.projects.set_status(project.id, ProjectStatus.OUTLINE_READY.value, timestamp, lease)
        self.tasks.complete(task.id, lease_token=task.lease_token)
        logger.info(
            "outline_ready project=%s version=%d sections=%d out_of_range=%d unsupported=%d"
            " without_facts=%d",
            project.id,
            version.version_number,
            len(sections),
            counters.out_of_range,
            counters.unsupported,
            counters.without_facts,
        )
        return version

    # ---------------------------------------------------------------- 查询

    def view(self, project_id: str) -> OutlineView:
        """版本列表与项目级当前状态（含最近一次生成任务的状态）。"""

        project = self._require_project(project_id)
        versions = tuple(self.outlines.versions(project.id, MAX_VERSIONS))
        confirmed = next(
            (item for item in versions if item.status == OutlineStatus.CONFIRMED.value), None
        )
        return OutlineView(
            versions=versions,
            latest_version_id=versions[0].id if versions else None,
            confirmed_version_id=confirmed.id if confirmed else None,
            blocking_count=self.runs.unresolved_blocking(project.id),
            task_status=self._task_status(project.id),
        )

    def detail(self, project_id: str, version_id: str) -> OutlineVersion:
        """单个版本；不属于该项目时按不存在处理。"""

        self._require_project(project_id)
        version = self.outlines.get(version_id)
        if version is None or version.project_id != project_id:
            raise DomainError("outline_not_found", "提纲版本不存在")
        return version

    def count(self, project_id: str) -> int:
        """项目内的提纲版本条数（项目详情的产物计数用，不受列表上限影响）。"""

        self._require_project(project_id)
        return self.outlines.count(project_id)

    def facts_of(self, version: OutlineVersion) -> list[Fact]:
        """版本绑定资料包里的事实（含引文），供编辑器勾选依据。"""

        if version.digest_run_id is None:
            return []
        return self.runs.facts(version.digest_run_id)

    # ---------------------------------------------------------------- 人工保存

    def save_human(
        self, project_id: str, base_version_id: str, draft: OutlineDraft
    ) -> OutlineVersion:
        """基于某一版保存人工稿：另存新版本，不改写被改的那一版。

        基准可以是项目内任意版本（含已确认与被取代的）：用户想从旧版改回来是正常需求。
        依据事实与引文仍按基准版本绑定的资料包校验，因此人工稿同样不能引用不存在的依据。
        """

        project = self._require_project(project_id)
        base = self.detail(project_id, base_version_id)
        facts = self.facts_of(base)
        if base.digest_run_id is None or not facts:
            raise DomainError(
                "outline_validation_failed", "这个版本绑定的资料包已不可用，无法校验依据"
            )
        fact_status, evidence_ids = fact_index(facts)
        sections, counters = normalize_outline(
            draft,
            fact_status=fact_status,
            evidence_ids=evidence_ids,
            max_sections=self.limits.max_sections,
            max_points=self.limits.max_points,
        )
        title, angle = normalize_title(draft)
        version = OutlineVersion(
            id=new_id(),
            project_id=project.id,
            version_number=self.outlines.latest_number(project.id) + 1,
            status=OutlineStatus.DRAFT.value,
            source=OutlineSource.HUMAN.value,
            title=title or base.title,
            angle=angle,
            sections=sections,
            material_snapshot_hash=base.material_snapshot_hash,
            digest_run_id=base.digest_run_id,
            # 人工版本没有模型产物：留空才不会把人工编辑算进提示词用量。
            prompt_version=None,
            model=None,
            task_id=None,
            based_on_version=base.id,
            created_at=now(),
            counters=counters,
        )
        self.outlines.add(version)
        logger.info(
            "outline_saved project=%s version=%d based_on=%d sections=%d",
            project.id,
            version.version_number,
            base.version_number,
            len(sections),
        )
        return version

    # ---------------------------------------------------------------- 确认

    def confirm(self, project_id: str, version_id: str) -> OutlineVersion:
        """确认一个提纲版本；已是该项目最新且已确认时幂等返回。

        只允许确认序号最大的版本：确认一个已经被更新版本取代的旧提纲，会让"已确认"
        指向一份用户已经改过的结构。需要确认旧版就基于它另存一版再确认。
        """

        project = self._require_project(project_id)
        version = self.detail(project_id, version_id)
        if version.status == OutlineStatus.SUPERSEDED.value:
            raise ConflictError("outline_version_superseded", "这个版本已被更新的版本取代")
        if version.version_number != self.outlines.latest_number(project.id):
            raise ConflictError("outline_version_stale", "只有最新的提纲版本可以确认")
        if version.is_confirmed:
            return version
        timestamp = now()
        updated = self.outlines.confirm(project.id, version.id, timestamp)
        if updated <= 0:
            # 并发下被更新的版本抢先：与前面的判断同一口径。
            raise ConflictError("outline_version_stale", "只有最新的提纲版本可以确认")
        self.projects.set_status(project.id, ProjectStatus.OUTLINE_CONFIRMED.value, timestamp, None)
        logger.info("outline_confirmed project=%s version=%d", project.id, version.version_number)
        return self.outlines.get(version.id) or version

    # ---------------------------------------------------------------- 内部

    def _reusable(self, existing: Task, digest_run_id: str) -> bool:
        """复用条件：任务未失败，且它用的正是这一次要用的资料包版本。

        同一批资料重新整理会产生新的 `digest_run_id`（事实可能不同），此时即使
        `materials_hash` 相同也必须新起任务，否则会拿到基于旧事实生成的提纲。
        """

        if existing.status not in {
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.SUCCEEDED.value,
        }:
            return False
        return str(existing.payload.get("digest_run_id", "")) == digest_run_id

    def _snapshot(self, existing: Task | None) -> dict[str, str]:
        """任务快照：Provider、模型与提示词版本固定下来，排队期间改配置不影响这次运行。"""

        if existing is not None:
            stored = {
                "provider_id": str(existing.payload.get("provider_id", "")),
                "model": str(existing.payload.get("model", "")),
                "prompt_id": str(existing.payload.get("prompt_id", "")),
                "prompt_version": str(existing.payload.get("prompt_version", "")),
            }
            if stored["provider_id"] and stored["model"] and stored["prompt_version"]:
                return stored
        prompt = self._active_prompt()
        provider, model = self.failover.require_available()
        return {
            "provider_id": provider.id,
            "model": model.model,
            "prompt_id": prompt.id,
            "prompt_version": prompt.version_string,
        }

    def _active_prompt(self) -> Prompt:
        prompt = self.prompts.active(TaskType.GENERATE_OUTLINE.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的提纲生成提示词")
        return prompt

    def _task_status(self, project_id: str) -> str | None:
        """最近一次提纲生成任务的状态。

        不取版本行上的 task_id（那只在成功时写入）：失败的尝试同样要显示出来，
        否则界面会把"超时失败"看成"生成完了但提纲是空的"。
        """

        for task in self.tasks.list(task_type=TaskType.GENERATE_OUTLINE.value, limit=20):
            if str(task.payload.get("project_id", "")) == project_id:
                return task.status
        return None

    def _lead_note(self, lead_id: str) -> str:
        lead = self.leads.get(lead_id)
        return (lead.note or "") if lead is not None else ""

    def _require_project(self, project_id: str) -> WritingProject:
        project = self.projects.get(project_id)
        if project is None:
            raise DomainError("project_not_found", "写作项目不存在")
        return project
