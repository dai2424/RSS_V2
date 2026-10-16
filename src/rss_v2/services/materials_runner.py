"""一次资料准备的执行器：检索式 → RSS 原文 → 联网补充 → 去重 → 预算与缺口。

与用例层分开：用例层管任务入队、项目状态与摘要落库；这里管"一次运行怎么跑"，
只依赖端口（检索、读取、模型）与资料仓储，不碰任务状态机。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from rss_v2.domain import (
    ArticleRead,
    DomainError,
    LeadMember,
    Material,
    MaterialKind,
    MaterialLimits,
    MaterialQueries,
    MaterialQuery,
    MaterialStatus,
    MaterialSummary,
    Prompt,
    Provider,
    ProviderKey,
    QueryOutcome,
    SearchHit,
    Task,
    TaskLease,
    TaskType,
    WritingProject,
)
from rss_v2.domain.prompts import render
from rss_v2.domain.values import new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import (
    ArticleReader,
    LeadRepository,
    MaterialRepository,
    MessageRepository,
    SearchClient,
)
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.materials_support import (
    MAX_CANDIDATES_PER_RUN,
    MAX_NOTES,
    MAX_SKIPPED_ROWS,
    interleave_hits,
    material_from_version,
    members_text,
    normalize_url,
    pick_queries,
    skipped_material,
    weak_row,
)
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MaterialRunner:
    """执行单个资料准备任务；每次模型调用都走 LLMFailover 与调用审计。"""

    materials: MaterialRepository  # 资料快照（按内容指纹去重）
    leads: LeadRepository  # 选题成员（消息版本）
    messages: MessageRepository  # 取版本正文
    prompts: PromptResolver  # 任务快照里的提示词版本
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与审计
    search: SearchClient  # 联网检索
    reader: ArticleReader  # 原文读取
    cooldown_seconds: int  # 临时错误冷却秒数

    def run(
        self,
        task: Task,
        project: WritingProject,
        limits: MaterialLimits,
        lease: TaskLease | None,
    ) -> MaterialSummary:
        """按任务快照读资料并写回摘要；失败抛出，由 worker 分类与重试。"""

        self.materials.clear_budget_skips(project.id, lease)
        members = self._members(project.lead_id, task)
        prompt = self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
        queries = self._queries(
            task, project.title, self._lead_note(project.lead_id), members, prompt, limits
        )
        summary = MaterialSummary()
        rss_summary, reads = self._store_rss(project.id, task, members, limits, lease)
        summary = summary.merged(rss_summary)
        summary = summary.merged(self._store_web(project.id, task, queries, reads, limits, lease))
        # 摘要与任务收尾由用例层统一处理：执行器只负责"读到了什么"。
        return replace(summary, finished_at=now(), notes=tuple(summary.notes[:MAX_NOTES]))

    def _store_rss(
        self,
        project_id: str,
        task: Task,
        members: Sequence[LeadMember],
        limits: MaterialLimits,
        lease: TaskLease | None,
    ) -> tuple[MaterialSummary, int]:
        """把成员消息版本落成 RSS 资料；正文为空的短消息再按链接回读一次页面。

        返回（本次计数, 已用的网络读取条数）：RSS 快照不占读取额度，回读页面才占。
        """

        timestamp = now()
        summary = MaterialSummary()
        reads = 0
        for member in members:
            version = self.messages.get_version(member.version_id)
            if version is None:
                summary = summary.merged(
                    MaterialSummary(
                        failed=1,
                        notes=(f"成员版本已不存在：{member.title[:40]}",),
                    )
                )
                continue
            content = (version.content or "").strip()
            text = content or (version.summary or "").strip()
            status = (
                MaterialStatus.READ.value
                if content
                else (MaterialStatus.SUMMARY_ONLY.value if text else MaterialStatus.FAILED.value)
            )
            snapshot = material_from_version(project_id, task, version, status, text, timestamp)
            url = normalize_url(version.url)
            if url and self.materials.by_url(project_id, url) is not None:
                # 同一 URL 一个项目只保留一行：同一链接的不同消息版本只计数，不再存第二行。
                summary = summary.merged(MaterialSummary(duplicates=1))
                continue
            stored = self.materials.add(snapshot, lease)
            summary = summary.merged(
                MaterialSummary(
                    read=1 if status == MaterialStatus.READ.value else 0,
                    summary_only=1 if status == MaterialStatus.SUMMARY_ONLY.value else 0,
                    failed=1 if status == MaterialStatus.FAILED.value else 0,
                    duplicates=0 if stored else 1,
                )
            )
            # 只有"连摘要都没有"的短消息才按链接回读补正文：有摘要就不回读，
            # 否则同一 URL 会存成两行（真实环境反馈的重复来源）。
            if text or not url:
                continue
            if reads >= limits.max_reads:
                summary = summary.merged(
                    MaterialSummary(
                        skipped_by_budget=1,
                        notes=(
                            f"读取上限 {limits.max_reads} 条已用完，未回读：{version.title[:30]}",
                        ),
                    )
                )
                continue
            reads += 1
            summary = summary.merged(
                self._read_and_store(
                    project_id, task, url, version.title, timestamp, lease, version.id
                )
            )
        return summary, reads

    def _store_web(
        self,
        project_id: str,
        task: Task,
        queries: Sequence[MaterialQuery],
        used_reads: int,
        limits: MaterialLimits,
        lease: TaskLease | None,
    ) -> MaterialSummary:
        """按检索式补充资料：命中只是候选，读取成功才算资料。

        候选按"每条检索式轮流取一条"排序后读取：预算不够时每条检索式都能拿到一部分，
        不会让第一条检索式把额度用光（实测中文检索式一次就给出 8 条命中）。
        超预算的候选留成"未读取"占位行（最多 MAX_SKIPPED_ROWS 条，总数记在摘要里）。
        """

        timestamp = now()
        reads = used_reads
        summary = MaterialSummary()
        seen_urls: set[str] = set()
        outcomes: list[QueryOutcome] = []
        queues: list[list[SearchHit]] = []
        for query in queries:
            # 检索失败（未配置、超时、限流、格式未开）直接抛出：整次任务冷却重试；
            # 已经写入的资料在重试时按地址跳过，进度不丢。
            outcome = self.search.search(query.text, limit=limits.search_result_limit)
            outcomes.append(
                QueryOutcome(
                    query=query.text,
                    purpose=query.purpose,
                    hits=len(outcome.hits),
                    unresponsive_engines=outcome.unresponsive_engines,
                )
            )
            if not outcome.hits and outcome.unresponsive_engines:
                # "没问到"和"没有资料"必须分开：引擎全被限流时结果为空不代表没有资料。
                summary = summary.merged(
                    MaterialSummary(
                        notes=(
                            "部分引擎未响应（"
                            + "、".join(outcome.unresponsive_engines[:3])
                            + f"），检索式：{query.text[:30]}",
                        )
                    )
                )
            fresh: list[SearchHit] = []
            for hit in outcome.hits:
                url = normalize_url(hit.url)
                if not url or url in seen_urls:
                    summary = summary.merged(MaterialSummary(duplicates=1))
                    continue
                seen_urls.add(url)
                existing = self.materials.by_url(project_id, url)
                if existing is not None and not weak_row(existing):
                    # 已经读到正文/摘要：不重复读、不重复存；失败/未读取的旧行放行重读。
                    summary = summary.merged(MaterialSummary(duplicates=1))
                    continue
                fresh.append(hit)
            queues.append(fresh)
        candidates = interleave_hits(queues, MAX_CANDIDATES_PER_RUN)
        skipped_rows = 0
        for hit in candidates:
            url = normalize_url(hit.url)
            if reads >= limits.max_reads:
                skipped_rows += 1
                summary = summary.merged(
                    MaterialSummary(
                        skipped_by_budget=1,
                        notes=(f"读取上限 {limits.max_reads} 条已用完，未读取：{hit.title[:30]}",),
                    )
                )
                if skipped_rows <= MAX_SKIPPED_ROWS:
                    self.materials.add(
                        skipped_material(project_id, task, url, hit.title, timestamp), lease
                    )
                continue
            reads += 1
            summary = summary.merged(
                self._read_and_store(project_id, task, url, hit.title, timestamp, lease)
            )
        return summary.merged(MaterialSummary(queries=tuple(outcomes)))

    def _read_and_store(
        self,
        project_id: str,
        task: Task,
        url: str,
        title: str,
        timestamp: int,
        lease: TaskLease | None,
        message_version_id: str | None = None,
    ) -> MaterialSummary:
        """读取一份网页并落库；返回这一份资料的计数。"""

        try:
            read = self.reader.read(url)
        except Exception as exc:  # noqa: BLE001 - 读取端口异常同样落成失败资料
            logger.warning("material_read_error url=%s class=%s", url, type(exc).__name__)
            read = ArticleRead(
                status=MaterialStatus.FAILED.value,
                final_url="",
                title=title,
                publisher="",
                published_at=None,
                text="",
                content_hash="",
                byte_size=0,
                error_code="material_read_error",
                error_message="读取发生内部错误，请查看运行日志",
            )
        material = Material(
            id=new_id(),
            project_id=project_id,
            kind=MaterialKind.WEB.value,
            status=read.status,
            url=url,
            final_url=read.final_url,
            title=read.title or title,
            publisher=read.publisher,
            published_at=read.published_at,
            fetched_at=now(),
            content_text=read.text,
            content_hash=read.content_hash,
            byte_size=read.byte_size,
            truncated=read.truncated,
            message_version_id=message_version_id,
            task_id=task.id,
            error_code=read.error_code,
            error_message=read.error_message,
            created_at=timestamp,
        )
        # 同一 URL 一个项目只保留一行：已有"可引用"的行（读到正文/摘要）时不再加第二条；
        # 旧行只是失败/不可用/未读取（没有证据价值）时，用这次的结果替换它，
        # 避免旧失败记录跨轮堆积（真实环境反馈的第二个问题）。
        existing = self.materials.by_url(project_id, url)
        if existing is not None:
            if not weak_row(existing) or read.status == MaterialStatus.FAILED.value:
                return MaterialSummary(duplicates=1)
            self.materials.delete(existing.id, lease)
        if self.materials.add(material, lease) is None:
            return MaterialSummary(duplicates=1)
        return MaterialSummary(
            read=1 if read.status == MaterialStatus.READ.value else 0,
            summary_only=1 if read.status == MaterialStatus.SUMMARY_ONLY.value else 0,
            unavailable=1 if read.status == MaterialStatus.UNAVAILABLE.value else 0,
            failed=1 if read.status == MaterialStatus.FAILED.value else 0,
        )

    def _queries(
        self,
        task: Task,
        title: str,
        summary: str,
        members: Sequence[LeadMember],
        prompt: Prompt,
        limits: MaterialLimits,
    ) -> list[MaterialQuery]:
        """调用模型生成检索式；条数按预算截断，重复与空文本丢弃并记日志。"""

        values = {
            "title": title,
            "summary": summary,
            "members": members_text(members),
        }
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[MaterialQueries, dict[str, int], int]:
            return self.provider.structured(
                provider,
                model_name,
                secret,
                payload,
                prompt.version_string,
                TaskType.PREPARE_MATERIALS.value,
                MaterialQueries,
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: MaterialQueries,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> list[MaterialQuery]:
            return pick_queries(result, limits.max_queries)

        return self.failover.execute(
            task.id,
            input_hash(payload.system, payload.user),
            prompt.version_string,
            self.cooldown_seconds,
            invoke,
            persist,
        )

    def _members(self, lead_id: str, task: Task) -> list[LeadMember]:
        """按任务快照取成员：执行期间消息更新不会改变这次任务的输入。"""

        wanted = [str(item) for item in task.payload.get("versions", [])]
        members = {member.version_id: member for member in self.leads.members(lead_id)}
        ordered = [members[version_id] for version_id in wanted if version_id in members]
        if not ordered:
            raise DomainError("project_members_empty", "选题成员快照已失效，请重新发起资料准备")
        return ordered

    def _lead_note(self, lead_id: str) -> str:
        lead = self.leads.get(lead_id)
        return (lead.note or "") if lead is not None else ""
