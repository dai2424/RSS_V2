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

import datetime
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import cast

from rss_v2.domain import (
    CandidateDraft,
    Category,
    DiscoveryBatch,
    DiscoveryCandidate,
    DiscoveryDigest,
    DiscoveryInputVersion,
    DiscoveryReasonSchema,
    DiscoveryStats,
    DiscoveryStatus,
    DomainError,
    ExternalServiceError,
    Lead,
    LeadMember,
    ProjectStatus,
    Prompt,
    Provider,
    ProviderKey,
    ProviderModel,
    ReasonKind,
    RecommendationReason,
    Task,
    TaskLease,
    TaskStatus,
    TaskType,
    WritingProject,
    lease_of,
)
from rss_v2.domain.prompts import render
from rss_v2.domain.values import new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import (
    CategoryRepository,
    DiscoveryRepository,
    LeadRepository,
    MessageScopeReader,
    SourceRepository,
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 候选标题/概要入库前的截断长度：标题还要塞进线索名（80）。
TITLE_LIMIT = 120
SUMMARY_LIMIT = 600
LEAD_TITLE_LIMIT = 80

#: 每条候选最多保留几条推荐理由。
MAX_REASONS = 3

#: 切批时允许的最短批长占上限的比例：避免真实数据里的零间隔把批次切碎。
MIN_BATCH_RATIO = 0.6

#: 输出被截断时把这一批对半拆开重试的最大层数（1 层 = 拆成两批，2 层 = 最多四批）。
MAX_SPLIT_DEPTH = 2

#: 输入文本里每条消息的摘要长度：先按这个长度渲染，超出批次上限再整体压缩。
SUMMARY_IN_PROMPT = 300
SUMMARY_IN_PROMPT_TIGHT = 120


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    """发现流程的预算与上限；由配置注入，测试可以传更小的值。"""

    window_days: int = 7  # 默认窗口天数
    max_window_days: int = 90  # 允许申请的最大跨度
    max_window_messages: int = 400  # 窗口内消息上限，超过要求缩小范围
    batch_size: int = 40  # 每批交给模型的消息条数
    batch_chars: int = 60000  # 单批输入字符上限
    max_members: int = 20  # 单个候选的成员上限
    max_candidates: int = 50  # 单次发现的候选上限
    max_output_tokens: int = 8192  # 单次模型调用的输出上限；截断会触发对半拆批


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
    prompts: PromptResolver  # 生效提示词
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与调用审计
    cooldown_seconds: int  # 临时错误冷却秒数
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
        fingerprint = _fingerprint(inputs)
        idempotency_key = f"discover:{_scope_hash(category_id, source_id)}:{fingerprint}"
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
            _batch_count(len(inputs), self.limits.batch_size),
        )
        return updated, False

    # ---------------------------------------------------------------- 执行

    def run(self, task: Task) -> DiscoveryBatch:
        """执行一个发现任务：分批调用模型、合并候选、落库并结束任务。

        失败直接抛出（由 worker 记录并决定重试）；重复执行（重试撞上已完成批次）
        直接收尾，不重复付费。
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
            inputs = self._task_inputs(task)
            prompt = self.prompts.snapshot(
                str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
            )
            drafts, stats = self._drafts(task, batch, inputs, prompt)
            kept, dropped = _rank(drafts, self.limits.max_candidates)
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

    def project_counts(self, project_id: str) -> dict[str, int]:
        """项目各阶段产物数量；资料/提纲/文章表在 P3a 起建立，本阶段全为 0。"""

        self.project_detail(project_id)
        return {"materials": 0, "facts": 0, "outlines": 0, "articles": 0}

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

    def _task_inputs(self, task: Task) -> list[tuple[str, str]]:
        raw: object = task.payload.get("versions")
        if not isinstance(raw, list):
            raise DomainError("discovery_input_missing", "任务缺少输入快照，请重新发起发现")
        # 任务快照是 JSON（dict[str, Any]）：JSON 数组对应 Python list，逐项校验长度后取值。
        pairs: list[tuple[str, str]] = []
        for item in cast(list[object], raw):
            entry = cast(list[object], item) if isinstance(item, list) else None
            if entry is None or len(entry) != 2:
                continue
            message_id, version_id = entry
            pairs.append((str(message_id), str(version_id)))
        return pairs

    def _drafts(
        self,
        task: Task,
        batch: DiscoveryBatch,
        inputs: Sequence[tuple[str, str]],
        prompt: Prompt,
    ) -> tuple[list[CandidateDraft], DiscoveryStats]:
        """按输入快照分批调用模型，返回候选草稿与累计计数。"""

        current = {
            item.version_id: item
            for item in self.scope.scope_versions(
                batch.window_start,
                batch.window_end,
                batch.source_id,
                batch.category_id,
                self.limits.max_window_messages,
                0,
            )
        }
        ordered = [current[version_id] for _, version_id in inputs if version_id in current]
        missing = len(inputs) - len(ordered)
        stats = DiscoveryStats(out_of_range=missing)
        if missing:
            logger.warning("discovery_input_missing batch=%s missing=%d", batch.id, missing)
        chunks = _chunks(ordered, max(1, self.limits.batch_size))
        drafts: list[CandidateDraft] = []
        for index, chunk in enumerate(chunks):
            batch_drafts, batch_stats = self._run_batch(
                task, batch, chunk, index + 1, len(chunks), prompt
            )
            drafts.extend(batch_drafts)
            stats = stats.merged(batch_stats)
        # 先跨批合并（相邻批重叠一条消息，跨切点的事件在这里并回一个候选），
        # 再全局消解：一条消息只能留在一个候选里。
        merged = _merge_drafts(drafts)
        resolved, conflict_stats = _resolve_conflicts(merged)
        return resolved, stats.merged(conflict_stats)

    def _run_batch(
        self,
        task: Task,
        batch: DiscoveryBatch,
        chunk: Sequence[DiscoveryInputVersion],
        index: int,
        total: int,
        prompt: Prompt,
        depth: int = 0,
    ) -> tuple[list[CandidateDraft], DiscoveryStats]:
        """调用模型处理一批消息，并把结果归一化为候选草稿。"""

        values = self._values(batch, chunk, index, total)
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[DiscoveryDigest, dict[str, int], int]:
            return self.provider.structured(
                provider,
                model_name,
                secret,
                payload,
                prompt.version_string,
                TaskType.DISCOVER_EVENTS.value,
                DiscoveryDigest,
                self.limits.max_output_tokens,
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            digest: DiscoveryDigest,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> tuple[list[CandidateDraft], DiscoveryStats]:
            return _normalize(digest, chunk, self.limits.max_members)

        try:
            return self.failover.execute(
                task.id,
                input_hash(payload.system, payload.user),
                prompt.version_string,
                self.cooldown_seconds,
                invoke,
                persist,
            )
        except ExternalServiceError as exc:
            # 一批消息太多时模型可能写到输出上限就中断（真实运行已出现）：把这一批对半
            # 拆开重试，而不是让整次发现失败。拆出的两批重叠一条，跨切点的事件仍能合并。
            if exc.code != "llm_output_truncated" or len(chunk) <= 1 or depth >= MAX_SPLIT_DEPTH:
                raise
            middle = len(chunk) // 2
            logger.info(
                "discovery_batch_split batch=%s size=%d depth=%d", batch.id, len(chunk), depth + 1
            )
            left, left_stats = self._run_batch(
                task, batch, chunk[: middle + 1], index, total, prompt, depth + 1
            )
            right, right_stats = self._run_batch(
                task, batch, chunk[middle:], index, total, prompt, depth + 1
            )
            return [*left, *right], left_stats.merged(right_stats)

    def _values(
        self,
        batch: DiscoveryBatch,
        chunk: Sequence[DiscoveryInputVersion],
        index: int,
        total: int,
    ) -> dict[str, str]:
        text = _messages_text(chunk, SUMMARY_IN_PROMPT, self.limits.batch_chars)
        if len(text) > self.limits.batch_chars:
            text = _messages_text(chunk, SUMMARY_IN_PROMPT_TIGHT, self.limits.batch_chars)
            logger.info("discovery_batch_tightened batch=%s chars=%d", batch.id, len(text))
        if len(text) > self.limits.batch_chars:
            # 仍然超限：硬截断并在日志里留痕；批次计数里能看出这次输入不完整。
            logger.warning("discovery_batch_truncated batch=%s chars=%d", batch.id, len(text))
            text = text[: self.limits.batch_chars]
        return {
            "window_start": str(batch.window_start),
            "window_end": str(batch.window_end),
            "scope": batch.source_label,
            "messages": text,
            "batch_index": str(index),
            "batch_total": str(total),
            "message_count": str(len(chunk)),
        }

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


def _batch_count(messages: int, size: int) -> int:
    """预估批次数：用于日志（真实切分会按时间间隔前后浮动一两条）。"""

    return (messages + max(1, size) - 1) // max(1, size)


def _chunks(
    ordered: Sequence[DiscoveryInputVersion], size: int
) -> list[list[DiscoveryInputVersion]]:
    """按条数上限切分输入，切点优先落在本批内最大的时间间隔之后，并重叠一条边界消息。

    两件事都是为了"同一事件不被拆到两批"：

    - 事件在时间上连续、事件之间通常有明显间隔，因此切在最大间隔处比"每满 N 条就切"
      更少切断事件；
    - 相邻批重叠最后一条消息，跨越切点的事件（前批尾部 + 后批头部）会在两批里都出现，
      于是共享成员，能被 _merge_drafts 并回一个候选——否则跨批事件只能拆成两个候选。
    """

    if size <= 1:
        return [[item] for item in ordered]
    # 批长下限：只在 [min_len, size] 区间里找切点。真实数据里同一分钟可能入多条消息
    # （间隔为 0），若允许在任意位置切，会出现"1 条一批"的碎片批，调用次数翻数倍。
    min_len = max(1, int(size * MIN_BATCH_RATIO))
    chunks: list[list[DiscoveryInputVersion]] = []
    index = 0
    total = len(ordered)
    while index < total:
        if total - index <= size:
            chunks.append(list(ordered[index:]))
            break
        cut = index + size
        best_gap = 0
        # 候选切点：第 position 条之前（position ∈ [index+min_len, index+size]）；
        # 同分取最后一个（>=），让切点尽量靠后、批次尽量装满。
        for position in range(index + min_len, index + size + 1):
            gap = ordered[position].event_time - ordered[position - 1].event_time
            if gap > 0 and gap >= best_gap:
                best_gap, cut = gap, position
        chunks.append(list(ordered[index:cut]))
        # 重叠一条：cut-1 既是这一批的尾巴，也是下一批的开头；至少前进一条避免死循环。
        index = max(index + 1, cut - 1)
    return chunks


def _fingerprint(inputs: Sequence[DiscoveryInputVersion]) -> str:
    """输入快照指纹：窗口内 (消息, 版本) 有序列表的哈希。"""

    digest = hashlib.sha256()
    for item in inputs:
        digest.update(f"{item.message_id}:{item.version_id}\n".encode())
    return digest.hexdigest()[:32]


def _scope_hash(category_id: str | None, source_id: str | None) -> str:
    """范围哈希：只含筛选条件（不含时间）。

    时间不进幂等键：重复点击时窗口会随请求时刻滑动，但只要有窗口没有新消息，
    输入指纹就相同，应该复用同一个批次而不是重复付费。批次里仍记录真实窗口，
    界面按批次显示"实际生效范围"。
    """

    digest = hashlib.sha256()
    digest.update(f"{category_id or ''}|{source_id or ''}".encode())
    return digest.hexdigest()[:16]


def _messages_text(
    chunk: Sequence[DiscoveryInputVersion], summary_limit: int, char_limit: int
) -> str:
    """把一批消息渲染成提示词里的行。

    每行：消息 ID、时间（UTC 秒与北京时间）、来源、标题、摘要、关键词。
    """

    lines: list[str] = []
    for item in chunk:
        parts = [
            f"[{item.message_id}]",
            f"{item.event_time}秒（{_beijing(item.event_time)}）",
            item.source_name,
        ]
        parts.append(item.title.replace("\n", " ").strip() or "（无标题）")
        summary = item.summary.replace("\n", " ").strip()
        if summary:
            parts.append(summary[:summary_limit])
        if item.keywords:
            parts.append("关键词：" + "、".join(item.keywords[:12]))
        lines.append(" | ".join(parts))
    return "\n".join(lines)[:char_limit]


#: 展示时区：提示词里同时给出 UTC 秒与北京时间，模型不必做换算。
_BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def _beijing(timestamp: int) -> str:
    """UTC 秒 → 北京时间字符串（提示词里给人看的时间）。"""

    return datetime.datetime.fromtimestamp(timestamp, _BEIJING).strftime("%m-%d %H:%M")


def _normalize(
    digest: DiscoveryDigest,
    chunk: Sequence[DiscoveryInputVersion],
    max_members: int,
) -> tuple[list[CandidateDraft], DiscoveryStats]:
    """把一批模型输出转成候选草稿：越界/批内重复/超限逐条计数，不静默丢弃。

    这里只处理**批内**冲突；跨批的重叠消息留给 _merge_drafts 合并、_resolve_conflicts 消解。
    """

    index = {item.message_id: item for item in chunk}
    seen: set[str] = set()
    drafts: list[CandidateDraft] = []
    out_of_range = 0
    duplicates = 0
    dropped_members = 0
    dropped_candidates = 0
    for raw in digest.candidates:
        picked: list[DiscoveryInputVersion] = []
        for message_id in raw.member_message_ids:
            item = index.get(message_id)
            if item is None:
                out_of_range += 1
                continue
            if message_id in seen:
                duplicates += 1
                continue
            seen.add(message_id)
            picked.append(item)
        if not picked:
            continue
        reasons = _reasons(raw.reasons)
        title = raw.title.strip()[:TITLE_LIMIT]
        if not title or not reasons:
            # 标题或理由为空说明这条候选无法解释给用户看，丢弃并计入"未纳入候选"。
            dropped_candidates += 1
            continue
        candidate_dropped = 0
        if len(picked) > max_members:
            candidate_dropped = len(picked) - max_members
            dropped_members += candidate_dropped
            picked = picked[:max_members]
        drafts.append(
            CandidateDraft(
                title=title,
                summary=(raw.summary.strip() or title)[:SUMMARY_LIMIT],
                event_time=_event_time(raw.event_time, picked),
                score=max(1, min(5, raw.score)),
                reasons=reasons,
                version_ids=tuple(item.version_id for item in picked),
                dropped_members=candidate_dropped,
            )
        )
    return drafts, DiscoveryStats(
        duplicate_members=duplicates,
        out_of_range=out_of_range,
        dropped_members=dropped_members,
        dropped_candidates=dropped_candidates,
    )


def _reasons(
    raw_reasons: Sequence[DiscoveryReasonSchema],
) -> tuple[RecommendationReason, ...]:
    """归一化推荐理由：只认 interesting/important，丢弃空文本，最多保留三条。"""

    reasons: list[RecommendationReason] = []
    for item in raw_reasons:
        kind = str(item.kind).strip().lower()
        text = str(item.text).strip()
        if kind in {"重要", "importance"}:
            kind = ReasonKind.IMPORTANT.value
        if kind in {"有趣", "interesting"}:
            kind = ReasonKind.INTERESTING.value
        if kind not in {ReasonKind.INTERESTING.value, ReasonKind.IMPORTANT.value} or not text:
            continue
        reasons.append(RecommendationReason(kind=kind, text=text[:200]))
        if len(reasons) >= MAX_REASONS:
            break
    return tuple(reasons)


def _event_time(raw: int | None, members: Sequence[DiscoveryInputVersion]) -> int | None:
    """事件时间：模型给出且合理时用它，否则回落到成员里最早的时间。"""

    if isinstance(raw, int) and raw > 0:
        return raw
    return min((item.event_time for item in members), default=None)


def _merge_drafts(drafts: Sequence[CandidateDraft]) -> list[CandidateDraft]:
    """跨批合并：共享任一成员版本即并集（确定、零额外调用）。"""

    if not drafts:
        return []
    parent = list(range(len(drafts)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owner: dict[str, int] = {}
    for index, draft in enumerate(drafts):
        for version_id in draft.version_ids:
            first = owner.setdefault(version_id, index)
            if first != index:
                union(first, index)
    groups: dict[int, list[CandidateDraft]] = {}
    for index, draft in enumerate(drafts):
        groups.setdefault(find(index), []).append(draft)
    return [_merge_group(group) for group in groups.values()]


def _resolve_conflicts(
    drafts: Sequence[CandidateDraft],
) -> tuple[list[CandidateDraft], DiscoveryStats]:
    """一条消息只能留在一个候选里：按推荐度排序后先到先得，后来者计数。

    合并之后仍可能有两个候选争抢同一条消息（例如重叠消息把两个不同事件桥接起来），
    此时保留更值得写的那个候选，另一个丢掉这条成员；成员被清空的候选整条丢弃。
    """

    duplicates = 0
    kept: list[CandidateDraft] = []
    seen: set[str] = set()
    for draft in sorted(drafts, key=_rank_key):
        members = tuple(version_id for version_id in draft.version_ids if version_id not in seen)
        duplicates += len(draft.version_ids) - len(members)
        if not members:
            continue
        seen.update(members)
        kept.append(
            draft if len(members) == len(draft.version_ids) else replace(draft, version_ids=members)
        )
    return kept, DiscoveryStats(duplicate_members=duplicates)


def _rank_key(draft: CandidateDraft) -> tuple[int, int, str]:
    """排序键：推荐度降序 → 事件时间早的在前 → 标题；跨批比较时稳定可复现。"""

    return (-draft.score, draft.event_time if draft.event_time is not None else 2**62, draft.title)


def _merge_group(group: Sequence[CandidateDraft]) -> CandidateDraft:
    if len(group) == 1:
        return group[0]
    best = max(group, key=lambda item: (item.score, -len(item.title)))
    members: dict[str, None] = {}
    for draft in group:
        for version_id in draft.version_ids:
            members.setdefault(version_id, None)
    reasons: dict[tuple[str, str], RecommendationReason] = {}
    for draft in group:
        for reason in draft.reasons:
            reasons.setdefault((reason.kind, reason.text), reason)
    times = [draft.event_time for draft in group if draft.event_time is not None]
    return CandidateDraft(
        title=best.title,
        summary=best.summary,
        event_time=min(times) if times else None,
        score=max(draft.score for draft in group),
        reasons=tuple(list(reasons.values())[:MAX_REASONS]),
        version_ids=tuple(members.keys()),
        dropped_members=sum(draft.dropped_members for draft in group),
    )


def _rank(
    drafts: Sequence[CandidateDraft], max_candidates: int
) -> tuple[list[CandidateDraft], int]:
    """排序并截断：推荐度降序 → 事件时间早的在前 → 标题；超出上限的部分计数。"""

    ordered = sorted(drafts, key=_rank_key)
    if len(ordered) <= max_candidates:
        return ordered, 0
    return ordered[:max_candidates], len(ordered) - max_candidates
