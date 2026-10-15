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

import datetime
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from rss_v2.domain import (
    BUDGET_EXHAUSTED,
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
    MessageVersion,
    ProjectStatus,
    Prompt,
    Provider,
    ProviderKey,
    QueryOutcome,
    SearchHit,
    Task,
    TaskLease,
    TaskStatus,
    TaskType,
    lease_of,
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
    TaskRepository,
    WritingProjectRepository,
)
from rss_v2.services.llm_failover import LLMFailover, input_hash
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

    materials: MaterialRepository
    projects: WritingProjectRepository
    leads: LeadRepository
    messages: MessageRepository
    tasks: TaskRepository
    prompts: PromptResolver
    provider: LLMProvider
    failover: LLMFailover
    search: SearchClient
    reader: ArticleReader
    cooldown_seconds: int
    limits: MaterialLimits = MaterialLimits()

    # ---------------------------------------------------------------- 入队

    def create(self, project_id: str) -> tuple[Task, bool]:
        """创建（或复用）资料准备任务；输入快照相同的重复请求不会重复付费。"""

        project = self._require_project(project_id)
        members = self.leads.members(project.lead_id)
        if not members:
            raise DomainError("project_members_empty", "这个选题的成员消息都已不存在，无法准备资料")
        snapshot = _snapshot_hash(members)
        key = f"materials:{project.id}:{snapshot}"
        existing = self.tasks.get_by_idempotency(key)
        if existing is not None and existing.status in {
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.SUCCEEDED.value,
        }:
            return existing, True
        snapshot_values = self._snapshot(existing)
        if existing is not None:
            key = f"{key}:retry:{now()}"
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
        """执行资料准备：检索式 → 读原文 → 联网补充 → 去重落库 → 写摘要。"""

        project_id = str(task.payload.get("project_id", ""))
        project = self._require_project(project_id)
        lease = lease_of(task)
        self.materials.clear_budget_skips(project.id, lease)
        members = self._members(project.lead_id, task)
        prompt = self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
        queries = self._queries(
            task, project.title, self._lead_note(project.lead_id), members, prompt
        )
        summary = MaterialSummary()
        # 1) RSS 原文：成员消息版本本身是订阅到的原文（不占网络读取额度）。
        rss_summary, reads = self._store_rss(project.id, task, members, lease)
        summary = summary.merged(rss_summary)
        # 2) 联网补充：检索命中读取成功才算资料，读取条数受预算约束。
        summary = summary.merged(self._store_web(project.id, task, queries, reads, lease))
        finished = replace(summary, finished_at=now(), notes=tuple(summary.notes[:MAX_NOTES]))
        self.projects.set_materials_summary(
            project.id,
            ProjectStatus.MATERIALS_READY.value,
            _summary_json(finished, task.id),
            finished.finished_at,
            lease,
        )
        self.tasks.complete(task.id, lease_token=task.lease_token)
        logger.info(
            "materials_ready project=%s read=%d summary_only=%d failed=%d duplicates=%d skipped=%d",
            project.id,
            finished.read,
            finished.summary_only,
            finished.failed,
            finished.duplicates,
            finished.skipped_by_budget,
        )
        return finished

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
        task_id = ""
        try:
            parsed: object = json.loads(project.materials_summary_json or "{}")
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            record = cast(dict[str, object], parsed)
            task_id = str(record.get("task_id") or "")
        status: str | None = None
        if task_id:
            task = self.tasks.get(task_id)
            status = task.status if task else None
        return project.materials_summary_json or "{}", status

    def detail(self, project_id: str, material_id: str) -> Material:
        """一条资料的完整快照（含正文）。"""

        self._require_project(project_id)
        material = self.materials.get(material_id)
        if material is None or material.project_id != project_id:
            raise DomainError("material_not_found", "资料不存在")
        return material

    # ---------------------------------------------------------------- 内部

    def _store_rss(
        self,
        project_id: str,
        task: Task,
        members: Sequence[LeadMember],
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
            stored = self.materials.add(
                _material_from_version(project_id, task, version, status, text, timestamp), lease
            )
            summary = summary.merged(
                MaterialSummary(
                    read=1 if status == MaterialStatus.READ.value else 0,
                    summary_only=1 if status == MaterialStatus.SUMMARY_ONLY.value else 0,
                    failed=1 if status == MaterialStatus.FAILED.value else 0,
                    duplicates=0 if stored else 1,
                )
            )
            # 短消息（电报/推特转发）只有摘要：按它的链接回读页面，尽力补上正文。
            url = _normalize_url(version.url)
            if content or not url:
                continue
            if reads >= self.limits.max_reads:
                summary = summary.merged(
                    MaterialSummary(
                        skipped_by_budget=1,
                        notes=(
                            f"读取上限 {self.limits.max_reads} 条已用完，"
                            f"未回读：{version.title[:30]}",
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
            outcome = self.search.search(query.text, limit=self.limits.search_result_limit)
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
                url = _normalize_url(hit.url)
                if not url or url in seen_urls:
                    summary = summary.merged(MaterialSummary(duplicates=1))
                    continue
                seen_urls.add(url)
                if self.materials.by_url(project_id, url) is not None:
                    summary = summary.merged(MaterialSummary(duplicates=1))
                    continue
                fresh.append(hit)
            queues.append(fresh)
        candidates = _interleave(queues, MAX_CANDIDATES_PER_RUN)
        skipped_rows = 0
        for hit in candidates:
            url = _normalize_url(hit.url)
            if reads >= self.limits.max_reads:
                skipped_rows += 1
                summary = summary.merged(
                    MaterialSummary(
                        skipped_by_budget=1,
                        notes=(
                            f"读取上限 {self.limits.max_reads} 条已用完，未读取：{hit.title[:30]}",
                        ),
                    )
                )
                if skipped_rows <= MAX_SKIPPED_ROWS:
                    self.materials.add(
                        _skipped_material(project_id, task, url, hit.title, timestamp), lease
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
    ) -> list[MaterialQuery]:
        """调用模型生成检索式；条数按预算截断，重复与空文本丢弃并记日志。"""

        values = {
            "title": title,
            "summary": summary,
            "members": _members_text(members),
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
            return _pick_queries(result, self.limits.max_queries)

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
        candidate = self.failover.first_available()
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        provider, model = candidate
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


def _pick_queries(result: MaterialQueries, limit: int) -> list[MaterialQuery]:
    """归一化检索式：去空、去重、按预算截断。"""

    picked: list[MaterialQuery] = []
    seen: set[str] = set()
    for item in result.queries:
        text = " ".join(item.text.split())
        if not text or text in seen:
            continue
        seen.add(text)
        picked.append(MaterialQuery(text=text[:120], purpose=" ".join(item.purpose.split())[:120]))
        if len(picked) >= max(1, limit):
            break
    return picked


def _interleave(queues: Sequence[Sequence[SearchHit]], limit: int) -> list[SearchHit]:
    """按"每条检索式轮流取一条"合并候选，最多取 limit 条。

    这样预算不够时每条检索式都有机会被读到，而不是第一条检索式把额度用光。
    """

    merged: list[SearchHit] = []
    depth = 0
    while len(merged) < limit:
        progressed = False
        for queue in queues:
            if depth < len(queue):
                merged.append(queue[depth])
                progressed = True
                if len(merged) >= limit:
                    break
        if not progressed:
            break
        depth += 1
    return merged


def _members_text(members: Sequence[LeadMember]) -> str:
    lines: list[str] = []
    for index, member in enumerate(members, start=1):
        published = _beijing(member.published_at or member.collected_at)
        lines.append(
            f"{index}. {member.title or '（无标题）'}（来源：{member.source_name}，{published}）"
        )
    return "\n".join(lines)


def _beijing(timestamp: int) -> str:
    return datetime.datetime.fromtimestamp(
        timestamp, datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M")


def _normalize_url(url: str) -> str:
    """规范化 URL：去 fragment、主机小写、去掉跟踪参数；只保留 http/https。"""

    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair and not pair.split("=", 1)[0].lower().startswith(_TRACKING_PREFIXES)
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ""))


def _snapshot_hash(members: Sequence[LeadMember]) -> str:
    digest = hashlib.sha256()
    for member in members:
        digest.update(f"{member.message_id}:{member.version_id}\n".encode())
    return digest.hexdigest()[:24]


def _material_from_version(
    project_id: str,
    task: Task,
    version: MessageVersion,
    status: str,
    text: str,
    timestamp: int,
) -> Material:
    return Material(
        id=new_id(),
        project_id=project_id,
        kind=MaterialKind.RSS.value,
        status=status,
        url=version.url,
        final_url="",
        title=version.title,
        publisher="",
        published_at=version.published_at,
        fetched_at=timestamp,
        content_text=text,
        content_hash=hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
        if text
        else "",
        byte_size=len(text.encode("utf-8")),
        truncated=False,
        message_version_id=version.id,
        task_id=task.id,
        error_code="" if text else "message_content_empty",
        error_message="" if text else "这条消息没有正文也没有摘要，无法作为证据",
        created_at=timestamp,
    )


def _skipped_material(
    project_id: str, task: Task, url: str, title: str, timestamp: int
) -> Material:
    return Material(
        id=new_id(),
        project_id=project_id,
        kind=MaterialKind.WEB.value,
        status=MaterialStatus.FAILED.value,
        url=url,
        final_url="",
        title=title,
        publisher="",
        published_at=None,
        fetched_at=timestamp,
        content_text="",
        content_hash="",
        byte_size=0,
        truncated=False,
        message_version_id=None,
        task_id=task.id,
        error_code=BUDGET_EXHAUSTED,
        error_message="这一轮读取预算已用完，未读取；可重新发起资料准备",
        created_at=timestamp,
    )


def _summary_json(summary: MaterialSummary, task_id: str) -> str:
    return json.dumps(
        {
            "queries": [
                {
                    "query": item.query,
                    "purpose": item.purpose,
                    "hits": item.hits,
                    "unresponsive_engines": list(item.unresponsive_engines),
                    "error_code": item.error_code,
                }
                for item in summary.queries
            ],
            "read": summary.read,
            "summary_only": summary.summary_only,
            "unavailable": summary.unavailable,
            "failed": summary.failed,
            "duplicates": summary.duplicates,
            "skipped_by_budget": summary.skipped_by_budget,
            "notes": list(summary.notes),
            "task_id": task_id,
            "finished_at": summary.finished_at,
        },
        ensure_ascii=False,
    )
