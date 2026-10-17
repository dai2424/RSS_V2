"""资料整理用例：把已读资料整理成事实、分歧与缺口，供用户逐项核对。

一次运行（一个任务）：

1. **输入**：项目已读资料（read / summary_only）的正文片段 + 选题标题与概要；
   输入指纹 = 材料 (ID, 内容指纹) 的哈希 → 同一资料包重复点击复用同一任务。
2. **整理**：模型按 schema 输出 facts（含 material_id + 原样引文）、conflicts、gaps。
3. **校验与落库**：引文必须在对应资料正文里能定位（宽松匹配标点差异，但拒绝改写）；
   越界材料与找不到的引文逐条计数；没有引文的事实记为 `missing` 并自动生成阻塞缺口。
4. **版本**：每次整理写一个 `digest_run`，历史版本与旧依据保留；已处理的缺口/分歧
   在重跑时保留处理结果（不会又变回待处理）。

未解决的关键缺口会写进项目摘要：`has_blocking` 为真时，后续提纲与全文阶段应拒绝
把这份资料包当成"可发布前提"。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from rss_v2.domain import (
    DigestCounts,
    DigestLimits,
    DigestOutcome,
    DigestRun,
    DomainError,
    Evidence,
    ExternalServiceError,
    Fact,
    FactDraft,
    Material,
    MaterialDigest,
    MaterialStatus,
    Note,
    NoteDraft,
    NoteKind,
    ProjectStatus,
    Prompt,
    Provider,
    ProviderKey,
    Task,
    TaskLease,
    TaskStatus,
    TaskType,
    lease_of,
)
from rss_v2.domain.facts_rules import (
    mark_conflicts,
    materials_fingerprint,
    materials_text,
    normalize_digest,
    quote_hash,
)
from rss_v2.domain.prompts import render
from rss_v2.domain.values import new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import (
    FactRepository,
    LeadRepository,
    MaterialRepository,
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 输出被截断时把这一批对半拆开重试的最大层数（1 层 = 拆成两批，2 层 = 最多四批）。
MAX_SPLIT_DEPTH = 2

#: 能作为证据的资料状态：只有读到正文或摘要的才算已读。
_EVIDENCE_STATUSES = {MaterialStatus.READ.value, MaterialStatus.SUMMARY_ONLY.value}

#: 摘要里最多保留几条缺口说明。
MAX_NOTES = 8


@dataclass(frozen=True, slots=True)
class DigestDetail:
    """一次整理的完整视图：版本 + 事实 + 待办。"""

    run: DigestRun
    facts: tuple[Fact, ...]
    notes: tuple[Note, ...]

    @property
    def has_blocking(self) -> bool:
        return any(note.blocking and note.resolved_at is None for note in self.notes)


@dataclass(slots=True)
class DigestService:
    """资料整理的创建、执行、查询与待办处理。"""

    runs: FactRepository  # 版本、事实、证据与待办
    materials: MaterialRepository  # 已读资料（证据来源）
    projects: WritingProjectRepository  # 项目状态与摘要
    leads: LeadRepository  # 选题概要（线索备注）
    tasks: TaskRepository  # 整理任务
    prompts: PromptResolver  # 生效提示词
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与审计
    cooldown_seconds: int
    limits: DigestLimits = DigestLimits()

    # ---------------------------------------------------------------- 入队

    def create(self, project_id: str, force: bool = False) -> tuple[Task, bool]:
        """创建（或复用）整理任务。

        默认同一资料包不重复付费；`force=True` 是用户主动的"重新整理"，用于换过模型
        或提示词、或想重跑一遍的情况——仍按版本追加，不改写历史依据。
        """

        project = self._require_project(project_id)
        evidence = self._evidence_materials(project_id)
        if not evidence:
            raise DomainError("digest_no_materials", "这个项目还没有可用的资料，请先完成资料准备")
        fingerprint = materials_fingerprint(
            [(material.id, material.content_hash) for material in evidence]
        )
        key = f"digest:{project.id}:{fingerprint}"
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
        snapshot = self._snapshot(existing)
        if existing is not None:
            key = f"{key}:{'run' if force else 'retry'}:{now()}"
        task = self.tasks.create(
            Task(
                id=new_id(),
                task_type=TaskType.DIGEST_MATERIALS,
                idempotency_key=key,
                status=TaskStatus.QUEUED,
                attempts=0,
                lease_until=None,
                input_version_id=None,
                output_version_id=None,
                payload={
                    "project_id": project.id,
                    "materials_hash": fingerprint,
                    "material_ids": [material.id for material in evidence],
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

    def run(self, task: Task) -> DigestOutcome:
        """执行整理：渲染资料 → 调用模型 → 校验引文 → 落库版本与事实。

        **只有产出结果的尝试才写版本行**：失败或超时的尝试不留"0 事实的版本"，
        否则界面会把一次失败的尝试显示成"整理完成但没有产出事实"。失败原因由任务
        行记录（任务列表与项目页都能看到错误分类与尝试次数）。
        """

        project_id = str(task.payload.get("project_id", ""))
        project = self._require_project(project_id)
        lease = lease_of(task)
        materials = self._task_materials(task)
        drafts, notes, counters = self._drafts(
            task, project.title, self._lead_note(project.lead_id), materials
        )
        run_id = new_id()
        self.runs.create_run(run_id, project.id, str(task.payload.get("materials_hash", "")), now())
        try:
            outcome = self._store(run_id, project.id, task, drafts, notes, counters, lease)
        except Exception:
            # 落库中途失败（例如租约失效）：连同事实/证据/待办一起删掉这个半成品版本，
            # 不留"0 事实的版本"给界面误读成"整理完成但没有产出"。
            self.runs.delete_run(run_id)
            raise
        blocking = sum(1 for note in outcome.notes if note.blocking and note.resolved_at is None)
        self.projects.set_digest_summary(
            project.id,
            ProjectStatus.MATERIALS_READY.value,
            json.dumps(
                {
                    "run_id": run_id,
                    "task_id": task.id,
                    "materials_hash": outcome.run.materials_hash,
                    "counts": _counts_json(outcome.run),
                    "blocking": blocking,
                    "notes": list(outcome.run.notes),
                    "finished_at": outcome.run.created_at,
                },
                ensure_ascii=False,
            ),
            outcome.run.created_at,
            lease,
        )
        self.tasks.complete(task.id, lease_token=task.lease_token)
        logger.info(
            "digest_ready project=%s facts=%d evidence=%d conflicts=%d gaps=%d"
            " blocking=%d rejected=%d",
            project.id,
            len(outcome.facts),
            outcome.run.counts.evidence,
            outcome.run.counts.conflict,
            outcome.run.counts.gap,
            blocking,
            outcome.run.counts.rejected_quotes,
        )
        return outcome

    # ---------------------------------------------------------------- 查询

    def detail(self, project_id: str, run_id: str | None = None) -> DigestDetail:
        """某个版本的整理结果；默认取最新版本。"""

        self._require_project(project_id)
        target = run_id or self.runs.latest_run(project_id)
        if target is None:
            raise DomainError("digest_not_found", "这个项目还没有资料整理记录")
        run = self.runs.get_run(target)
        if run is None or run.project_id != project_id:
            raise DomainError("digest_not_found", "资料整理版本不存在")
        return DigestDetail(
            run=run, facts=tuple(self.runs.facts(run.id)), notes=tuple(self.runs.notes(run.id))
        )

    def latest(self, project_id: str) -> DigestDetail | None:
        """最新版本；没有整理过时返回 None（界面显示"还没有整理"）。"""

        target = self.runs.latest_run(project_id)
        return self.detail(project_id, target) if target else None

    def runs_for(self, project_id: str, limit: int = 10) -> list[DigestRun]:
        """版本列表（新到旧），用于查看历史资料包。"""

        self._require_project(project_id)
        return [
            run
            for run in (self.runs.get_run(run_id) for run_id in self.runs.runs(project_id, limit))
            if run is not None
        ]

    def blocking(self, project_id: str) -> int:
        """未处理的阻塞缺口数（后续阶段据此判断能否进入提纲）。"""

        return self.runs.unresolved_blocking(project_id)

    def summary_view(self, project_id: str) -> tuple[str, str | None]:
        """项目上的整理摘要与**最近一次整理任务**的状态。

        任务状态不取摘要里的 task_id（那只在成功时写入）：失败的尝试同样要显示出来，
        否则界面会把"超时失败"看成"整理完了但没产出事实"。这里按任务表里最近一次
        `digest_materials` 任务的 payload 归属项目来判断。
        """

        project = self._require_project(project_id)
        raw = project.digest_summary_json or "{}"
        status: str | None = None
        for task in self.tasks.list(task_type=TaskType.DIGEST_MATERIALS.value, limit=20):
            if str(task.payload.get("project_id", "")) == project.id:
                status = task.status
                break
        return raw, status

    def resolve_note(self, project_id: str, note_id: str) -> Note:
        """标记缺口/分歧已处理；重复处理是幂等的（返回当前记录）。"""

        self._require_project(project_id)
        note = self.runs.get_note(note_id)
        if note is None or note.project_id != project_id:
            raise DomainError("note_not_found", "这条待办不存在")
        if note.resolved_at is None:
            self.runs.resolve_note(note_id, now())
            note = self.runs.get_note(note_id) or note
        return note

    # ---------------------------------------------------------------- 内部

    def _drafts(
        self,
        task: Task,
        title: str,
        summary: str,
        materials: Sequence[Material],
    ) -> tuple[list[FactDraft], list[NoteDraft], dict[str, int]]:
        """按批整理资料：每批一次模型调用，返回合并后的事实与待办草稿。

        真实环境实测：把整份资料包放进一次调用时，事实与引文会写不完（输出上限截断、
        JSON 不完整）——因此按 `batch_size` 分批，相邻批重叠一份资料；输出仍被截断时
        再对半拆（见 `_run_batch`）。冲突里引用的"第几条事实"按批内偏移换算成全局序号。
        """

        prompt = self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
        chunks = _chunks(materials, self.limits.batch_size)
        facts: list[FactDraft] = []
        notes: list[NoteDraft] = []
        counters = {"out_of_range": 0, "rejected_quotes": 0}
        for chunk in chunks:
            chunk_facts, chunk_notes, chunk_counters = self._run_batch(
                task, title, summary, chunk, prompt
            )
            offset = len(facts)
            facts.extend(chunk_facts)
            notes.extend(
                replace(note, fact_indexes=tuple(index + offset for index in note.fact_indexes))
                for note in chunk_notes
            )
            counters["out_of_range"] += chunk_counters.get("out_of_range", 0)
            counters["rejected_quotes"] += chunk_counters.get("rejected_quotes", 0)
        if len(facts) > self.limits.max_facts:
            # 全部批次合计仍受事实条数上限约束：丢弃的部分在待办里写明，不静默。
            dropped = len(facts) - self.limits.max_facts
            facts = facts[: self.limits.max_facts]
            notes.append(
                NoteDraft(
                    kind=NoteKind.NOTE.value,
                    text=f"事实条数超过上限（{self.limits.max_facts}），已丢弃 {dropped} 条",
                )
            )
        return facts, notes, counters

    def _run_batch(
        self,
        task: Task,
        title: str,
        summary: str,
        materials: Sequence[Material],
        prompt: Prompt,
        depth: int = 0,
    ) -> tuple[list[FactDraft], list[NoteDraft], dict[str, int]]:
        """整理一批资料；输出被截断时把这一批对半拆开重试（两半重叠一份资料）。"""

        values = {
            "title": title,
            "summary": summary,
            "materials": self._materials_text(materials),
        }
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[MaterialDigest, dict[str, int], int]:
            return self.provider.structured(
                provider,
                model_name,
                secret,
                payload,
                prompt.version_string,
                TaskType.DIGEST_MATERIALS.value,
                MaterialDigest,
                self.limits.max_output_tokens,
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: MaterialDigest,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> MaterialDigest:
            return result

        try:
            digest = self.failover.execute(
                task.id,
                input_hash(payload.system, payload.user),
                prompt.version_string,
                self.cooldown_seconds,
                invoke,
                persist,
            )
        except ExternalServiceError as exc:
            if (
                exc.code != "llm_output_truncated"
                or len(materials) <= 1
                or depth >= MAX_SPLIT_DEPTH
            ):
                raise
            middle = len(materials) // 2
            logger.info("digest_batch_split task=%s size=%d", task.id, len(materials))
            left = self._run_batch(task, title, summary, materials[: middle + 1], prompt, depth + 1)
            right = self._run_batch(task, title, summary, materials[middle:], prompt, depth + 1)
            return (
                [*left[0], *right[0]],
                [*left[1], *right[1]],
                {
                    "out_of_range": left[2].get("out_of_range", 0)
                    + right[2].get("out_of_range", 0),
                    "rejected_quotes": left[2].get("rejected_quotes", 0)
                    + right[2].get("rejected_quotes", 0),
                },
            )
        facts, notes, counters = normalize_digest(
            digest,
            {material.id: material.content_text for material in materials},
            max_facts=self.limits.max_facts,
            max_supports=self.limits.max_evidence_per_fact,
        )
        marked, marked_notes = mark_conflicts(facts, notes)
        return marked, marked_notes, counters

    def _materials_text(self, materials: Sequence[Material]) -> str:
        entries: list[tuple[str, str, str, str]] = []
        for material in materials:
            text = material.content_text[: self.limits.material_chars]
            source = material.publisher or material.final_url or material.url
            entries.append((material.id, material.title, source, text))
        return materials_text(entries, self.limits.max_input_chars)

    def _evidence_materials(self, project_id: str) -> list[Material]:
        """能作为证据的资料：已读（含仅摘要）且正文非空。"""

        return [
            material
            for material in self.materials.list_with_content(project_id)
            if material.status in _EVIDENCE_STATUSES and material.content_text.strip()
        ]

    def _task_materials(self, task: Task) -> list[Material]:
        """按任务快照取资料：执行期间新增资料不会改变这次任务的输入。"""

        wanted = [str(item) for item in task.payload.get("material_ids", [])]
        by_id = {
            material.id: material
            for material in self._evidence_materials(str(task.payload.get("project_id", "")))
        }
        ordered = [by_id[item] for item in wanted if item in by_id]
        if not ordered:
            raise DomainError("digest_materials_missing", "资料快照已失效，请重新发起整理")
        return ordered

    def _store(
        self,
        run_id: str,
        project_id: str,
        task: Task,
        drafts: Sequence[FactDraft],
        notes: Sequence[NoteDraft],
        counters: dict[str, int],
        lease: TaskLease | None,
    ) -> DigestOutcome:
        timestamp = now()
        facts: list[Fact] = []
        evidence_count = 0
        by_index: dict[int, Fact] = {}
        for index, draft in enumerate(drafts):
            fact_id = new_id()
            evidence_ids: list[str] = []
            for support, locator in zip(draft.supports, draft.locators, strict=False):
                evidence = Evidence(
                    id=new_id(),
                    run_id=run_id,
                    project_id=project_id,
                    material_id=support.material_id,
                    quote=support.quote,
                    locator=locator,
                    quote_hash=quote_hash(support.quote),
                    created_at=timestamp,
                )
                self.runs.add_evidence(evidence, lease)
                evidence_ids.append(evidence.id)
                evidence_count += 1
            fact = Fact(
                id=fact_id,
                run_id=run_id,
                project_id=project_id,
                kind=draft.kind,
                statement=draft.statement,
                status=draft.status,
                conflict_note=draft.conflict_note,
                conflict_refs=(),
                created_at=timestamp,
            )
            self.runs.add_fact(fact, evidence_ids, lease)
            facts.append(fact)
            by_index[index] = fact
        stored_notes: list[Note] = []
        for draft in notes:
            first_index = draft.fact_indexes[0] if draft.fact_indexes else None
            fact = by_index.get(first_index) if first_index is not None else None
            note = Note(
                id=new_id(),
                run_id=run_id,
                project_id=project_id,
                kind=draft.kind,
                text=draft.text,
                fact_id=fact.id if fact else None,
                blocking=draft.blocking,
                resolved_at=None,
                created_at=timestamp,
            )
            self.runs.add_note(note, lease)
            stored_notes.append(note)
        counts = DigestCounts(
            fact=sum(1 for draft in drafts if draft.kind == "fact"),
            analysis=sum(1 for draft in drafts if draft.kind == "analysis"),
            speculation=sum(1 for draft in drafts if draft.kind == "speculation"),
            evidence=evidence_count,
            conflict=sum(1 for note in notes if note.kind == NoteKind.CONFLICT.value),
            gap=sum(1 for note in notes if note.kind == NoteKind.GAP.value),
            blocking=sum(1 for note in notes if note.blocking),
            rejected_quotes=counters.get("rejected_quotes", 0),
            out_of_range=counters.get("out_of_range", 0),
        )
        notes_text = [
            f"{'阻塞缺口' if note.blocking else '提示'}：{note.text}"
            for note in stored_notes
            if note.kind in {NoteKind.GAP.value, NoteKind.CONFLICT.value}
        ][:MAX_NOTES]
        self.runs.finish_run(run_id, task.id, counts, notes_text, lease)
        run = replace(self.runs.get_run(run_id) or _empty_run(run_id, project_id), counts=counts)
        return DigestOutcome(run=run, facts=tuple(facts), notes=tuple(stored_notes))

    def _snapshot(self, existing: Task | None) -> dict[str, str]:
        if existing is not None:
            stored = {
                "provider_id": str(existing.payload.get("provider_id", "")),
                "model": str(existing.payload.get("model", "")),
                "prompt_id": str(existing.payload.get("prompt_id", "")),
                "prompt_version": str(existing.payload.get("prompt_version", "")),
            }
            if stored["provider_id"] and stored["model"] and stored["prompt_version"]:
                return stored
        prompt = self.prompts.active(TaskType.DIGEST_MATERIALS.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的资料整理提示词")
        provider, model = self.failover.require_available()
        return {
            "provider_id": provider.id,
            "model": model.model,
            "prompt_id": prompt.id,
            "prompt_version": prompt.version_string,
        }

    def _lead_note(self, lead_id: str) -> str:
        lead = self.leads.get(lead_id)
        return (lead.note or "") if lead is not None else ""

    def _require_project(self, project_id: str):
        project = self.projects.get(project_id)
        if project is None:
            raise DomainError("project_not_found", "写作项目不存在")
        return project


def _counts_json(run: DigestRun) -> dict[str, int]:
    return {
        "fact": run.counts.fact,
        "analysis": run.counts.analysis,
        "speculation": run.counts.speculation,
        "evidence": run.counts.evidence,
        "conflict": run.counts.conflict,
        "gap": run.counts.gap,
        "blocking": run.counts.blocking,
        "rejected_quotes": run.counts.rejected_quotes,
        "out_of_range": run.counts.out_of_range,
    }


def _empty_run(run_id: str, project_id: str) -> DigestRun:
    return DigestRun(id=run_id, project_id=project_id, materials_hash="", task_id=None)


def _chunks(materials: Sequence[Material], size: int) -> list[list[Material]]:
    """按条数切分资料，相邻批重叠一份，保证跨切点的同一事件两边都能看到。"""

    limit = max(1, size)
    if len(materials) <= limit:
        return [list(materials)]
    chunks: list[list[Material]] = []
    index = 0
    while index < len(materials):
        chunk = list(materials[index : index + limit])
        chunks.append(chunk)
        if index + limit >= len(materials):
            break
        index += limit - 1  # 重叠一份
    return chunks
