"""全文用例：按已确认提纲生成、查询与预览。

一次运行（一个任务）：

1. **输入**：任务快照里的提纲版本（`outline_version_id`）与它绑定的资料整理版本
   （`digest_run_id`）——任务排队期间重新确认提纲不会改变这次运行的输入。
2. **生成**：模型按 `ArticleDraft` 输出分节正文、引用表与来源候选；服务端逐条核对
   引用归属、段落类型与来源，并把引文原文、来源信息**快照**进版本。
3. **版本**：每次生成追加一个 `generated`+`draft` 版本，历史版本不改写；同一提纲
   与资料包重复点击复用同一任务，`force` 才新起版本。
4. **预览**：用版本快照渲染受控 HTML，并带上引用证据与未处理的缺口/分歧。

**未处理的阻塞缺口不拦生成**，只随列表与预览返回，由界面显著标记。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rss_v2.domain import (
    ArticleKind,
    ArticleLimits,
    ArticleStatus,
    ArticleVersion,
    ArticleView,
    DomainError,
    Fact,
    Note,
    OutlineVersion,
    ProjectStatus,
    Prompt,
    Task,
    TaskStatus,
    TaskType,
    WritingProject,
    lease_of,
)
from rss_v2.domain.article_markdown import render_article_markdown
from rss_v2.domain.article_rules import MaterialInfo, normalize_article
from rss_v2.domain.outline_rules import facts_text, notes_text, outline_text
from rss_v2.domain.values import new_id, now
from rss_v2.ports import (
    ArticleRepository,
    FactRepository,
    LeadRepository,
    MaterialRepository,
    OutlineRepository,
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.article_preview import ArticlePreview, build_preview
from rss_v2.services.article_runner import ArticleRunner
from rss_v2.services.llm_failover import LLMFailover
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 列表与详情最多返回几个版本（历史版本只用于回溯，不需要全量）。
MAX_VERSIONS = 20


@dataclass(slots=True)
class ArticleService:
    """全文的创建、执行、查询与预览。"""

    articles: ArticleRepository  # 全文版本
    outlines: OutlineRepository  # 已确认的提纲版本（生成的前置）
    runs: FactRepository  # 资料包版本、事实与待办
    materials: MaterialRepository  # 资料行（来源列表与引文快照）
    projects: WritingProjectRepository  # 项目状态
    leads: LeadRepository  # 选题备注（作为切入点提示）
    tasks: TaskRepository  # 生成任务
    prompts: PromptResolver  # 生效提示词
    failover: LLMFailover  # 取可用 Provider×模型（任务快照）
    runner: ArticleRunner  # 一次生成的执行器
    limits: ArticleLimits = ArticleLimits()

    # ---------------------------------------------------------------- 入队

    def create(self, project_id: str, force: bool = False) -> tuple[Task, bool]:
        """创建（或复用）全文生成任务。

        前置是项目已有**已确认的提纲**：全文必须使用用户确认过的那一版结构。已确认
        版本不是最新版本（确认后又改过）时仍按它生成——契约要求"全文使用确认时的
        提纲版本"，界面会提示存在未确认的更新版本。
        """

        project = self._require_project(project_id)
        outline = self.outlines.confirmed(project.id)
        if outline is None:
            raise DomainError("outline_not_confirmed", "这个项目还没有已确认的提纲，请先确认提纲")
        key = f"article:{project.id}:{outline.id}:{outline.material_snapshot_hash}"
        existing = self.tasks.get_by_idempotency(key)
        if not force and existing is not None and self._reusable(existing, outline.id):
            return existing, True
        snapshot = self._snapshot(existing)
        if existing is not None:
            # 同一提纲但上次失败、或用户主动重新生成：另起一个任务与版本。
            key = f"{key}:{'run' if force else 'retry'}:{now()}"
        task = self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.GENERATE_ARTICLE,
                idempotency_key=key,
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=outline.id,
                output_version_id=None,
                payload={
                    "project_id": project.id,
                    "outline_version_id": outline.id,
                    "digest_run_id": outline.digest_run_id,
                    "material_snapshot_hash": outline.material_snapshot_hash,
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

    def run(self, task: Task) -> ArticleVersion:
        """执行生成：渲染输入 → 调用模型 → 校验引用 → 落库版本并推进项目状态。

        **只有通过校验的产物才写版本行**：模型输出不合法（悬空标记、事实段没有引用、
        没有正文）或调用失败都不留半成品；原因由任务行记录，界面据此区分"模型没按
        约定作答"与"写不出来"。
        """

        project = self._require_project(str(task.payload.get("project_id", "")))
        lease = lease_of(task)
        outline = self._outline_of(project.id, str(task.payload.get("outline_version_id", "")))
        facts = self._facts_of(outline)
        if not facts:
            raise DomainError("article_no_facts", "这次资料整理没有产出可用事实，无法生成全文")
        notes = self._notes_of(outline)
        draft = self.runner.run(
            task,
            title=project.title,
            summary=self._lead_note(project.lead_id),
            outline=outline_text(outline, limit=self.limits.max_input_chars),
            facts=facts_text(
                facts, limit=self.limits.max_input_chars, fact_chars=self.limits.fact_chars
            ),
            notes=notes_text(notes, limit=self.limits.max_input_chars),
        )
        title, lead, body, counters = normalize_article(
            draft,
            facts=facts,
            materials=self._materials_by_id(project.id),
            limits=self.limits,
            fallback_title=outline.title or project.title,
        )
        timestamp = now()
        version = ArticleVersion(
            id=new_id(),
            project_id=project.id,
            version_number=self.articles.latest_number(project.id) + 1,
            kind=ArticleKind.GENERATED.value,
            status=ArticleStatus.DRAFT.value,
            title=title,
            lead=lead,
            body=body,
            # Markdown 永远由校验后的结构派生，不采用模型原文。
            body_markdown=render_article_markdown(title, lead, body),
            outline_version_id=outline.id,
            material_snapshot_hash=outline.material_snapshot_hash,
            digest_run_id=outline.digest_run_id,
            prompt_version=str(task.payload.get("prompt_version", "")) or None,
            model=str(task.payload.get("model", "")) or None,
            task_id=task.id,
            base_article_version_id=None,
            created_at=timestamp,
            counters=counters,
        )
        self.articles.add(version, lease)
        self.projects.set_status(project.id, ProjectStatus.ARTICLE_READY.value, timestamp, lease)
        self.tasks.complete(task.id, lease_token=task.lease_token)
        logger.info(
            "article_ready project=%s version=%d sections=%d citations=%d sources=%d"
            " out_of_range=%d downgraded=%d",
            project.id,
            version.version_number,
            len(body.sections),
            len(body.citations),
            version.source_count,
            counters.out_of_range,
            counters.downgraded_speculation,
        )
        return version

    # ---------------------------------------------------------------- 查询

    def view(self, project_id: str) -> ArticleView:
        """版本列表与项目级当前状态（含最近一次生成任务的状态）。"""

        project = self._require_project(project_id)
        versions = tuple(self.articles.versions(project.id, MAX_VERSIONS))
        confirmed = self.outlines.confirmed(project.id)
        saved = self.articles.saved(project.id)
        return ArticleView(
            versions=versions,
            latest_version_id=versions[0].id if versions else None,
            saved_version_id=saved.id if saved is not None else None,
            outline_version_id=confirmed.id if confirmed is not None else None,
            has_unconfirmed_changes=(
                confirmed is not None
                and self.outlines.latest_number(project.id) != confirmed.version_number
            ),
            blocking_count=self.runs.unresolved_blocking(project.id),
            task_status=self._task_status(project.id),
        )

    def detail(self, project_id: str, version_id: str) -> ArticleVersion:
        """单个版本；不属于该项目时按不存在处理。"""

        self._require_project(project_id)
        version = self.articles.get(version_id)
        if version is None or version.project_id != project_id:
            raise DomainError("article_not_found", "文章版本不存在")
        return version

    def count(self, project_id: str) -> int:
        """项目内的全文版本条数（项目详情的产物计数用）。"""

        self._require_project(project_id)
        return self.articles.count(project_id)

    def preview(self, project_id: str, version_id: str) -> ArticlePreview:
        """预览载荷：受控 HTML + 引用证据 + 未处理的缺口/分歧。"""

        version = self.detail(project_id, version_id)
        return build_preview(
            version,
            self._notes_of_version(version),
            self.runs.unresolved_blocking(project_id),
        )

    # ---------------------------------------------------------------- 内部

    def _outline_of(self, project_id: str, outline_id: str) -> OutlineVersion:
        outline = self.outlines.get(outline_id)
        if outline is None or outline.project_id != project_id:
            raise DomainError("outline_not_found", "提纲版本已失效，请重新确认提纲")
        return outline

    def _facts_of(self, outline: OutlineVersion) -> list[Fact]:
        if outline.digest_run_id is None:
            return []
        return self.runs.facts(outline.digest_run_id)

    def _notes_of(self, outline: OutlineVersion) -> list[Note]:
        if outline.digest_run_id is None:
            return []
        return self.runs.notes(outline.digest_run_id)

    def _notes_of_version(self, version: ArticleVersion) -> list[Note]:
        if version.digest_run_id is None:
            return []
        return self.runs.notes(version.digest_run_id)

    def _materials_by_id(self, project_id: str) -> dict[str, MaterialInfo]:
        """资料 id → （标题, 发布者, 链接）；链接优先用跟随重定向后的地址。"""

        return {
            material.id: (
                material.title,
                material.publisher,
                material.final_url or material.url,
            )
            for material in self.materials.list(project_id)
        }

    def _reusable(self, existing: Task, outline_version_id: str) -> bool:
        """复用条件：任务未失败，且它用的正是这一次要用的提纲版本。

        只比资料包哈希不够：同一批资料可能确认过多个提纲版本（改结构后重新确认），
        那样会复用一份基于旧提纲的任务。
        """

        if existing.status not in {
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.SUCCEEDED.value,
        }:
            return False
        return str(existing.payload.get("outline_version_id", "")) == outline_version_id

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
        prompt = self.prompts.active(TaskType.GENERATE_ARTICLE.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的全文生成提示词")
        return prompt

    def _task_status(self, project_id: str) -> str | None:
        """最近一次全文生成任务的状态；失败的尝试同样要显示。"""

        for task in self.tasks.list(task_type=TaskType.GENERATE_ARTICLE.value, limit=20):
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
