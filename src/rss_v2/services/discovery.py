"""选题发现用例：把时间窗口内的消息交给模型按事件分组，落成可选择的候选。

流程与边界（见 docs/决策记录/2026-10-15-事件选题发现与聚合.md）：

1. **入队**：固定窗口（默认最近 7 天）、可选分类/来源；把窗口内消息的
   (消息, 版本) 快照指纹写进批次，幂等键 = 范围 + 输入指纹。因此重复点击、
   窗口轻微滑动但输入没变时都直接复用既有批次，不会重复付费。
2. **执行**：按输入顺序分批（每批 `batch_size` 条）调用模型；模型只能给出
   **本批输入里的消息 ID**，版本由服务端解析；越界、重复、超限一律丢弃并计数，
   不静默吞掉。跨批共享成员即并集合并。
3. **落库**：候选的事件成员写进线索（kind=discovery），候选行只存事件级信息；
   写入前校验任务租约，晚到的 worker 不能污染结果。

关键词只作为附加上下文传入，**不作为入选门槛**：窗口内没有加工结果的消息同样参与
分组，否则短快讯永远进不了选题（P1a 复核结论）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from rss_v2.domain import (
    CandidateDraft,
    Category,
    DiscoveryBatch,
    DiscoveryCandidate,
    DiscoveryInputVersion,
    DiscoveryLimits,
    DiscoveryStats,
    DiscoveryStatus,
    DomainError,
    Lead,
    LeadMember,
    ProjectStatus,
    Prompt,
    Provider,
    ProviderModel,
    Task,
    TaskLease,
    TaskStatus,
    TaskType,
    WritingProject,
    lease_of,
)
from rss_v2.domain.discovery_rules import (
    LEAD_TITLE_LIMIT,
    TITLE_LIMIT,
    batch_count,
    input_fingerprint,
    rank_candidates,
    scope_hash,
)
from rss_v2.domain.values import new_id, now
from rss_v2.ports import (
    CategoryRepository,
    DiscoveryRepository,
    LeadRepository,
    MessageScopeReader,
    SourceRepository,
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.discovery_runner import DiscoveryRunner
from rss_v2.services.llm_failover import LLMFailover
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CandidateDetail:
    """候选详情：候选本体 + 成员（消息版本）+ 已建立的写作项目。"""

    candidate: DiscoveryCandidate
    members: tuple[LeadMember, ...]
    project_id: str | None


@dataclass(frozen=True, slots=True)
class DiscoveryDetail:
    """批次详情：批次本体与候选列表。"""

    batch: DiscoveryBatch
    candidates: tuple[CandidateDetail, ...]


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """任务里冻结的模型与提示词版本：重试沿用同一份，不重新挑选。"""

    provider_id: str
    model: str
    prompt_id: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class BatchView:
    """批次的展示视图：补上分类/来源名与当前任务状态。

    任务状态与批次状态分开存：批次记的是"上次执行结果"，任务记的是"现在是否在跑"。
    界面优先显示任务状态（排队/运行），否则显示批次结果（完成/失败）——重试期间批次
    仍保留上次的失败原因，不会被显示成"排队中"的假状态。
    """

    batch: DiscoveryBatch
    category_name: str | None
    source_name: str | None
    task_status: str | None


@dataclass(slots=True)
class DiscoveryService:
    """选题发现的创建、执行、查询与选择。"""

    batches: DiscoveryRepository  # 批次与候选
    projects: WritingProjectRepository  # 写作项目
    leads: LeadRepository  # 候选的事件成员（消息版本）
    scope: MessageScopeReader  # 窗口内消息读取（含无加工结果的消息）
    sources: SourceRepository  # 范围校验与展示名
    categories: CategoryRepository  # 展示名
    tasks: TaskRepository  # 批次任务
    prompts: PromptResolver  # 生效提示词（入队时取当前版本，重试用任务快照）
    failover: LLMFailover  # 首选模型候选（入队快照）
    runner: DiscoveryRunner  # 一次执行的模型调用与归一化
    limits: DiscoveryLimits = DiscoveryLimits()

    # ---------------------------------------------------------------- 入队

    def create(
        self,
        window_start: int | None = None,
        window_end: int | None = None,
        category_id: str | None = None,
        source_id: str | None = None,
    ) -> tuple[DiscoveryBatch, bool]:
        """创建（或复用）一个发现批次，返回 (批次, 是否复用)。

        复用条件：同样筛选范围内、输入消息版本快照完全相同。失败批次可再次点击
        重试——同一个批次行，新建一个任务。
        """

        timestamp = now()
        start, end = self._window(window_start, window_end, timestamp)
        category = self._category(category_id, source_id)
        message_count = self.scope.count_scope_versions(start, end, source_id, category_id)
        if message_count > self.limits.max_window_messages:
            raise DomainError(
                "discovery_window_too_large",
                f"该范围内有 {message_count} 条消息，超过 "
                f"{self.limits.max_window_messages} 条上限，请缩小时间范围或增加分类/来源筛选",
            )
        inputs = self.scope.scope_versions(start, end, source_id, category_id, message_count, 0)
        fingerprint = input_fingerprint(inputs)
        idempotency_key = f"discover:{scope_hash(category_id, source_id)}:{fingerprint}"
        existing = self.batches.get_by_idempotency(idempotency_key)
        if existing is not None and existing.status != DiscoveryStatus.FAILED.value:
            return existing, True
        label = self._scope_label(source_id, category)
        if not inputs:
            # 空窗口不是错误：直接落成"已完成、0 个候选"，界面显示空态而不是失败。
            batch = DiscoveryBatch(
                id=new_id(),
                window_start=start,
                window_end=end,
                category_id=category_id,
                source_id=source_id,
                input_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                status=DiscoveryStatus.SUCCEEDED.value,
                task_id=None,
                source_label=label,
                message_count=0,
                candidate_count=0,
                duplicate_members=0,
                out_of_range=0,
                dropped_members=0,
                dropped_candidates=0,
                error_code=None,
                error_message=None,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self.batches.create(batch)
            return batch, False
        if existing is not None:
            # 重试沿用上次的模型与提示词快照：模型配置改了也不换，Key 冷却不影响入队。
            snapshot = self._stored_snapshot(existing) or self._fresh_snapshot()
            task = self._enqueue_task(
                existing, inputs, f"{idempotency_key}:retry:{timestamp}", snapshot
            )
            updated = replace(
                existing,
                status=DiscoveryStatus.QUEUED.value,
                task_id=task.id,
                message_count=len(inputs),
                error_code=None,
                error_message=None,
                updated_at=timestamp,
            )
            self.batches.update(updated)
            return updated, False
        batch = DiscoveryBatch(
            id=new_id(),
            window_start=start,
            window_end=end,
            category_id=category_id,
            source_id=source_id,
            input_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            status=DiscoveryStatus.QUEUED.value,
            task_id=None,
            source_label=label,
            message_count=len(inputs),
            candidate_count=0,
            duplicate_members=0,
            out_of_range=0,
            dropped_members=0,
            dropped_candidates=0,
            error_code=None,
            error_message=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        snapshot = self._fresh_snapshot()
        self.batches.create(batch)
        task = self._enqueue_task(batch, inputs, idempotency_key, snapshot)
        updated = replace(batch, task_id=task.id)
        self.batches.update(updated)
        logger.info(
            "discovery_enqueued batch=%s messages=%d batches=%d",
            updated.id,
            updated.message_count,
            batch_count(len(inputs), self.limits.batch_size),
        )
        return updated, False

    # ---------------------------------------------------------------- 执行

    def run(self, task: Task) -> DiscoveryBatch:
        """执行一个发现任务：交给执行器跑模型，再把候选与计数写回批次。

        重复执行（重试撞上已完成批次）直接收尾，不重复付费；失败记录批次原因后原样
        抛出，由 worker 分类并决定重试。
        """

        batch = self._require_batch(str(task.payload.get("batch_id", "")))
        if batch.status == DiscoveryStatus.SUCCEEDED.value:
            self.tasks.complete(task.id, lease_token=task.lease_token)
            return batch
        lease = lease_of(task)
        batch = replace(
            batch,
            status=DiscoveryStatus.RUNNING.value,
            task_id=task.id,
            error_code=None,
            error_message=None,
            updated_at=now(),
        )
        self.batches.update(batch, lease)
        try:
            drafts, stats = self.runner.run(task, batch, self.limits)
            kept, dropped = rank_candidates(drafts, self.limits.max_candidates)
            stats = stats.merged(DiscoveryStats(dropped_candidates=dropped))
            candidates = self._store(batch, kept, lease)
            result = replace(
                batch,
                status=DiscoveryStatus.SUCCEEDED.value,
                candidate_count=len(candidates),
                duplicate_members=stats.duplicate_members,
                out_of_range=stats.out_of_range,
                dropped_members=stats.dropped_members,
                dropped_candidates=stats.dropped_candidates,
                error_code=None,
                error_message=None,
                updated_at=now(),
            )
            self.batches.update(result, lease)
            self.tasks.complete(task.id, lease_token=task.lease_token)
            logger.info(
                "discovery_finished batch=%s candidates=%d out_of_range=%d duplicates=%d",
                result.id,
                result.candidate_count,
                result.out_of_range,
                result.duplicate_members,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - 记录批次失败原因后原样抛出，由 worker 分类
            self._record_failure(batch, exc, lease)
            raise

    # ---------------------------------------------------------------- 查询

    def list(
        self, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> tuple[list[DiscoveryBatch], int]:
        """批次列表与总数，按创建时间倒序。"""

        return self.batches.list(limit, offset, status)

    def detail(self, batch_id: str) -> DiscoveryDetail:
        """批次详情：候选 + 成员（消息版本）+ 已建立的写作项目。"""

        batch = self._require_batch(batch_id)
        details: list[CandidateDetail] = []
        for candidate in self.batches.candidates(batch.id):
            project = self.projects.get_by_candidate(candidate.id)
            details.append(
                CandidateDetail(
                    candidate=candidate,
                    members=tuple(self.leads.members(candidate.lead_id)),
                    project_id=project.id if project else None,
                )
            )
        return DiscoveryDetail(batch=batch, candidates=tuple(details))

    def batch_view(self, batch: DiscoveryBatch) -> BatchView:
        """批次展示视图：补分类/来源名与当前任务状态（界面据此显示排队/运行）。"""

        task_status: str | None = None
        if batch.task_id:
            task = self.tasks.get(batch.task_id)
            task_status = task.status if task else None
        category_name: str | None = None
        if batch.category_id:
            category = self.categories.get(batch.category_id)
            category_name = category.name if category else None
        source_name: str | None = None
        if batch.source_id:
            source = self.sources.get(batch.source_id)
            source_name = source.name if source else None
        return BatchView(
            batch=batch,
            category_name=category_name,
            source_name=source_name,
            task_status=task_status,
        )

    def select(self, batch_id: str, candidate_id: str) -> tuple[WritingProject, bool]:
        """选定候选并建立写作项目；同一候选重复选择返回既有项目。"""

        batch = self._require_batch(batch_id)
        if batch.status != DiscoveryStatus.SUCCEEDED.value:
            raise DomainError("discovery_not_ready", "这次发现还没有完成，请等它跑完再选题")
        candidate = self.batches.candidate(candidate_id)
        if candidate is None or candidate.batch_id != batch.id:
            raise DomainError("discovery_candidate_not_found", "候选不存在")
        existing = self.projects.get_by_candidate(candidate_id)
        if existing is not None:
            return existing, True
        timestamp = now()
        project = self.projects.create(
            WritingProject(
                id=new_id(),
                lead_id=candidate.lead_id,
                candidate_id=candidate.id,
                title=candidate.title[:TITLE_LIMIT],
                status=ProjectStatus.MATERIALS_PENDING.value,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        logger.info("writing_project_created project=%s candidate=%s", project.id, candidate.id)
        return project, False

    def delete(self, batch_id: str) -> int:
        """删除批次（连同候选与无人引用的自动线索）。"""

        if self.batches.delete(batch_id) == 0:
            raise DomainError("discovery_batch_not_found", "发现批次不存在")
        return 1

    def project_detail(self, project_id: str) -> tuple[WritingProject, tuple[LeadMember, ...]]:
        """写作项目与其成员（消息版本）；P3a 起在项目上继续扩展资料与提纲。"""

        project = self.projects.get(project_id)
        if project is None:
            raise DomainError("project_not_found", "写作项目不存在")
        return project, tuple(self.leads.members(project.lead_id))

    def list_projects(
        self, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> tuple[list[WritingProject], int]:
        """写作项目列表与总数。"""

        return self.projects.list(limit, offset, status)

    # ---------------------------------------------------------------- 内部

    def _window(
        self, window_start: int | None, window_end: int | None, timestamp: int
    ) -> tuple[int, int]:
        if window_start is None and window_end is None:
            return timestamp - self.limits.window_days * 86400, timestamp
        if window_start is None or window_end is None:
            raise DomainError("discovery_window_invalid", "起止时间需要同时给出")
        if window_end <= window_start:
            raise DomainError("discovery_window_invalid", "结束时间必须晚于开始时间")
        if window_end - window_start > self.limits.max_window_days * 86400:
            raise DomainError(
                "discovery_window_invalid",
                f"时间跨度不能超过 {self.limits.max_window_days} 天",
            )
        return window_start, window_end

    def _category(self, category_id: str | None, source_id: str | None) -> Category | None:
        """校验分类/来源组合：来源必须属于所选分类，否则筛选条件互相矛盾。"""

        category = None
        if category_id:
            category = self.categories.get(category_id)
            if category is None:
                raise DomainError("category_not_found", "分类不存在")
        if source_id:
            source = self.sources.get(source_id)
            if source is None:
                raise DomainError("source_not_found", "来源不存在")
            if category_id and source.category_id != category_id:
                raise DomainError(
                    "discovery_scope_mismatch", "所选来源不属于所选分类，请调整筛选条件"
                )
        return category

    def _scope_label(self, source_id: str | None, category: Category | None) -> str:
        """展示用范围说明：让界面和任务列表不必从时间戳猜范围。"""

        if source_id:
            source = self.sources.get(source_id)
            return source.name if source else "未知来源"
        if category is not None:
            return category.name
        return "全部订阅"

    def _active_prompt(self) -> Prompt:
        prompt = self.prompts.active(TaskType.DISCOVER_EVENTS.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的选题发现提示词")
        return prompt

    def _model(self) -> tuple[Provider, ProviderModel]:
        candidate = self.failover.first_available()
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        return candidate

    def _fresh_snapshot(self) -> TaskSnapshot:
        """入队时的模型与提示词快照：优先组合 + 该任务类型当前生效的提示词。"""

        prompt = self._active_prompt()
        provider, model = self._model()
        return TaskSnapshot(
            provider_id=provider.id,
            model=model.model,
            prompt_id=prompt.id,
            prompt_version=prompt.version_string,
        )

    def _stored_snapshot(self, batch: DiscoveryBatch) -> TaskSnapshot | None:
        """上次执行留下的快照；缺失时返回 None，由调用方重新挑选。"""

        if not batch.task_id:
            return None
        task = self.tasks.get(batch.task_id)
        if task is None:
            return None
        payload = task.payload
        snapshot = TaskSnapshot(
            provider_id=str(payload.get("provider_id") or ""),
            model=str(payload.get("model") or ""),
            prompt_id=str(payload.get("prompt_id") or ""),
            prompt_version=str(payload.get("prompt_version") or ""),
        )
        if not (snapshot.provider_id and snapshot.model and snapshot.prompt_version):
            return None
        return snapshot

    def _enqueue_task(
        self,
        batch: DiscoveryBatch,
        inputs: Sequence[DiscoveryInputVersion],
        key: str,
        snapshot: TaskSnapshot,
    ) -> Task:
        """把输入版本快照放进任务：执行时按这份快照取数，不等价于"再看一次窗口"。"""

        timestamp = now()
        task = Task(
            id=new_id(),
            task_type=TaskType.DISCOVER_EVENTS,
            idempotency_key=key,
            status=TaskStatus.QUEUED,
            attempts=0,
            lease_until=None,
            input_version_id=None,
            output_version_id=None,
            payload={
                "batch_id": batch.id,
                "versions": [[item.message_id, item.version_id] for item in inputs],
                "provider_id": snapshot.provider_id,
                "model": snapshot.model,
                "prompt_id": snapshot.prompt_id,
                "prompt_version": snapshot.prompt_version,
            },
            error_code=None,
            error_message=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        return self.tasks.create(task)

    def _store(
        self,
        batch: DiscoveryBatch,
        drafts: Sequence[CandidateDraft],
        lease: TaskLease | None,
    ) -> list[DiscoveryCandidate]:
        """落库：先建事件成员（线索），再用一个事务替换该批次的候选。"""

        timestamp = now()
        candidates: list[DiscoveryCandidate] = []
        for rank, draft in enumerate(drafts, start=1):
            lead = self.leads.create(
                Lead(
                    id=new_id(),
                    title=draft.title[:LEAD_TITLE_LIMIT],
                    note=draft.summary[:280] or None,
                    created_at=timestamp,
                    updated_at=timestamp,
                    kind="discovery",
                ),
                list(draft.version_ids),
            )
            candidates.append(
                DiscoveryCandidate(
                    id=new_id(),
                    batch_id=batch.id,
                    lead_id=lead.id,
                    rank=rank,
                    title=draft.title,
                    summary=draft.summary,
                    event_time=draft.event_time,
                    score=draft.score,
                    reasons=draft.reasons,
                    member_count=len(draft.version_ids),
                    dropped_members=draft.dropped_members,
                    created_at=timestamp,
                )
            )
        self.batches.replace_candidates(batch.id, candidates, lease)
        return candidates

    def _record_failure(
        self, batch: DiscoveryBatch, exc: Exception, lease: TaskLease | None
    ) -> None:
        """把失败写回批次；租约已失效时放弃写入（晚到的 worker 不能改状态）。

        这里不再抛异常：写失败原因不能掩盖真正的失败本身，worker 会照常记录任务错误。
        """

        code = getattr(exc, "code", "") or type(exc).__name__
        message = getattr(exc, "message", "") or str(exc)
        try:
            self.batches.update(
                replace(
                    batch,
                    status=DiscoveryStatus.FAILED.value,
                    error_code=str(code)[:80],
                    error_message=str(message)[:1000],
                    updated_at=now(),
                ),
                lease,
            )
        except Exception as skip:  # noqa: BLE001 - 记录失败原因的通道不能反过来打断失败处理
            logger.info("discovery_failure_skipped batch=%s reason=%s", batch.id, skip)

    def _require_batch(self, batch_id: str) -> DiscoveryBatch:
        batch = self.batches.get(batch_id)
        if batch is None:
            raise DomainError("discovery_batch_not_found", "发现批次不存在")
        return batch
