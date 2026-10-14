"""合并建议：把词表分批交给模型找同指候选，人工确认后走既有合并流程。

一批词表 = 一个后台任务：排队、重试、耗时与其它模型任务走同一套机制，任务列表里
看得见进度，也能一次排很多批让 worker 慢慢跑完。任务只负责"跑这一批"，结果写进建议表；
同一批只调用一次模型（批次台账记着），因此重复入队或重试都不会重复付费。

模型给的写法一律按"本批词表里的规范词键"回查：越界写法、幻觉写法直接丢弃并计数；
已在同一词条下、或已经建议过的组也不再返回——人工只需要看真正的新候选。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from rss_v2.domain import (
    KEYWORD_KIND_LABELS,
    DomainError,
    Keyword,
    KeywordEntry,
    MergeSuggestion,
    Prompt,
    Provider,
    ProviderKey,
    SuggestedGroup,
    SuggestionBatch,
    SuggestionEnqueue,
    SuggestionStatus,
    SuggestResult,
    Task,
    TaskStatus,
    TaskType,
    normalize_keyword,
)
from rss_v2.domain.prompts import render
from rss_v2.domain.values import new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import KeywordRepository, SuggestionRepository, TaskRepository
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 单批最大词数：再大就会让一次调用的输出难以核对，也容易超出上下文。
MAX_BATCH = 100

#: 批次文本每行是「- 写法（类型）」；模型常把类型标注一起抄回来。
#: 标注是我们自己渲染上去的，不属于写法，回查前先剥掉。
_BATCH_LABEL = re.compile(r"^(.+?)\s*（(?:实体|主题|事件)）$")

#: 默认批次大小：配置项 `llm_suggestion_batch_size` 覆盖它，批次台账的切分也按它走。
#: 实测每词约 1.4 秒、250 输出 token（含思考）：20 词约 40 秒，适配默认的 60 秒 Provider
#: 超时；60 词约 86 秒，需要把 Provider 超时调到 180 秒以上。
DEFAULT_BATCH_SIZE = 20

#: 一次最多排多少批：整表扫完大约十几批，给一个上限防止误点把队列灌满。
MAX_ENQUEUE = 50


@dataclass(slots=True)
class KeywordSuggestionService:
    """合并建议的生成、列表与处置。"""

    keywords: KeywordRepository  # 词表：切批、别名解析
    suggestions: SuggestionRepository  # 建议与批次台账
    tasks: TaskRepository  # 批次任务：入队、结束
    prompts: PromptResolver  # 生效提示词
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与调用审计
    cooldown_seconds: int  # 临时错误冷却秒数
    batch_size: int = DEFAULT_BATCH_SIZE  # 每批词数；界面显式传 size 时以传入值为准

    def enqueue(self, count: int = 1, size: int | None = None) -> SuggestionEnqueue:
        """把接下来的若干批排成任务；已经生成过或已经排过的批次不重复入队。"""

        prompt = self._active_prompt()
        batch_size = self._batch_size(size)
        batches, done = self._scan_batches(max(1, min(count, MAX_ENQUEUE)), batch_size)
        if not batches:
            return SuggestionEnqueue(
                enqueued=0, reused=0, done=done, first_offset=None, last_offset=None
            )
        candidate = self.failover.first_available()
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        provider, model = candidate
        enqueued = 0
        reused = 0
        for offset, fingerprint in batches:
            key = self.idempotency_key(fingerprint, offset)
            if self.tasks.get_by_idempotency(key) is not None:
                reused += 1
                continue
            self.tasks.create(
                Task(
                    id=new_id(),
                    task_type=TaskType.KEYWORD_MERGE_SUGGEST,
                    idempotency_key=key,
                    status=TaskStatus.QUEUED,
                    attempts=0,
                    lease_until=None,
                    input_version_id=None,
                    output_version_id=None,
                    payload={
                        "offset": offset,
                        "size": batch_size,
                        "fingerprint": fingerprint,
                        "provider_id": provider.id,
                        "model": model.model,
                        "prompt_id": prompt.id,
                        "prompt_version": prompt.version_string,
                    },
                    error_code=None,
                    error_message=None,
                    created_at=now(),
                    updated_at=now(),
                )
            )
            enqueued += 1
        logger.info(
            "suggestion_enqueued enqueued=%d reused=%d offset=%d size=%d",
            enqueued,
            reused,
            batches[0][0],
            batch_size,
        )
        return SuggestionEnqueue(
            enqueued=enqueued,
            reused=reused,
            done=done,
            first_offset=batches[0][0],
            last_offset=batches[-1][0],
        )

    def run(self, task: Task) -> SuggestionBatch:
        """执行一个批次任务：按任务快照渲染、调用模型、落库，最后结束任务。

        失败直接抛出，由 worker 记录并决定是否重试；这一批已经生成过（重复入队或重试
        撞上已完成的批次）时直接收尾，不再调用模型。
        """

        offset = int(task.payload.get("offset", 0))
        size = int(task.payload.get("size", self.batch_size))
        prompt = self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
        entries = self.keywords.vocabulary(None, None, 1, None, None, False, size, offset)
        total = self._total()
        fingerprint = _batch_fingerprint([item.key for item in entries])
        stored = self.suggestions.batch_result(fingerprint, offset)
        if stored is not None:
            self.tasks.complete(task.id, lease_token=task.lease_token)
            return SuggestionBatch(
                vocabulary_fingerprint=fingerprint,
                offset=offset,
                size=len(entries),
                total=total,
                groups=stored[0],
                dropped=stored[1],
                words=stored[2],
            )
        if not entries:
            self.tasks.complete(task.id, lease_token=task.lease_token)
            return SuggestionBatch(
                vocabulary_fingerprint=fingerprint,
                offset=offset,
                size=0,
                total=total,
                groups=0,
                dropped=0,
                words=0,
            )
        values = {"vocabulary": _batch_text(entries)}
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )
        prompt_version = prompt.version_string

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[SuggestResult, dict[str, int], int]:
            return self.provider.suggest_merges(
                provider, model_name, secret, payload, prompt_version
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: SuggestResult,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> SuggestionBatch:
            return self._store(fingerprint, offset, entries, total, result)

        batch = self.failover.execute(
            task.id,
            input_hash(payload.system, payload.user),
            prompt_version,
            self.cooldown_seconds,
            invoke,
            persist,
        )
        self.tasks.complete(task.id, lease_token=task.lease_token)
        return batch

    def status(self) -> SuggestionStatus:
        """任务进度与待确认数量；界面据此显示进度并决定是否轮询。"""

        kind = TaskType.KEYWORD_MERGE_SUGGEST.value
        return SuggestionStatus(
            queued=self.tasks.count(status="queued", task_type=kind),
            running=self.tasks.count(status="running", task_type=kind),
            failed=self.tasks.count(status="failed", task_type=kind),
            pending=self.suggestions.pending_count(),
            total=self._total(),
            scanned=min(self.suggestions.scanned_words(), self._total()),
        )

    def idempotency_key(self, fingerprint: str, offset: int) -> str:
        """一批一个任务，键里带内容指纹：词表变了，同一段就能重新排一次。"""

        return f"suggest:{fingerprint}:{offset}"

    def _batch_size(self, size: int | None) -> int:
        return max(2, min(size or self.batch_size, MAX_BATCH))

    def _total(self) -> int:
        return self.keywords.count_vocabulary(None, None, 1, None, None, False)

    def _active_prompt(self) -> Prompt:
        prompt = self.prompts.active(TaskType.KEYWORD_MERGE_SUGGEST.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的合并建议提示词")
        return prompt

    def _scan_batches(self, count: int, size: int) -> tuple[list[tuple[int, str]], int]:
        """从第 0 个词往后找该排的批次，返回 ([(偏移, 内容指纹)], 已生成过的批次数)。

        批次按顺序铺开，因此从头扫一遍即可：生成过的（批次台账里有）跳过，
        已经排过同样内容的（任务幂等键命中）也跳过，其余排成新任务。
        """

        total = self._total()
        batches: list[tuple[int, str]] = []
        done = 0
        start = 0
        while start < total:
            entries = self.keywords.vocabulary(None, None, 1, None, None, False, size, start)
            fingerprint = _batch_fingerprint([item.key for item in entries])
            if self.suggestions.batch_result(fingerprint, start) is not None:
                done += 1
            elif (
                self.tasks.get_by_idempotency(self.idempotency_key(fingerprint, start)) is not None
            ):
                pass  # 同样的内容已经排过：不重复入队，也不重复付费
            elif len(batches) < count:
                batches.append((start, fingerprint))
            else:
                break  # 已凑够要排的批次，后面的留到下次
            start += size
        return batches, done

    def list(self, status: str | None = "pending", limit: int = 50) -> list[MergeSuggestion]:
        """建议列表；默认只看待处理的。"""

        return self.suggestions.list(status, limit)

    def pending(self) -> int:
        """待处理建议数。"""

        return self.suggestions.pending_count()

    def dismiss(self, suggestion_id: str) -> None:
        """忽略一条建议；忽略过的组不会再被建议。"""

        self.suggestions.dismiss(suggestion_id, now())

    def accept(self, suggestion_id: str, merge_id: str) -> None:
        """标记建议已采纳；合并本身已经由既有端点完成。"""

        self.suggestions.accept(suggestion_id, merge_id, now())

    def _store(
        self,
        fingerprint: str,
        offset: int,
        entries: list[KeywordEntry],
        total: int,
        result: SuggestResult,
    ) -> SuggestionBatch:
        """把模型输出折算成可落库的建议：越界与重复的组丢弃并计数。"""

        known = {item.key: item for item in entries}
        seen = self.suggestions.existing_fingerprints()
        items: list[MergeSuggestion] = []
        dropped = 0
        for group in result.groups:
            members = self._members(group, known)
            if len(members) < 2:
                dropped += 1
                logger.info("suggestion_dropped reason=out_of_batch terms=%s", list(group.members))
                continue
            group_fingerprint = _group_fingerprint([item.normalized for item in members])
            if group_fingerprint in seen:
                dropped += 1
                continue
            seen.add(group_fingerprint)
            target_key = self._target_key(group.target, members)
            items.append(
                MergeSuggestion(
                    id=new_id(),
                    members=tuple(members),
                    target_key=target_key,
                    target_text=next(
                        (item.text for item in members if item.normalized == target_key),
                        target_key,
                    ),
                    rationale=group.rationale.strip(),
                    status="pending",
                    batch_offset=offset,
                    created_at=now(),
                    resolved_at=None,
                    merge_id=None,
                )
            )
        stored = self.suggestions.save_batch(
            fingerprint, offset, items, dropped, len(entries), now()
        )
        logger.info("suggestion_batch offset=%d groups=%d dropped=%d", offset, len(stored), dropped)
        return SuggestionBatch(
            vocabulary_fingerprint=fingerprint,
            offset=offset,
            size=len(entries),
            total=total,
            groups=len(stored),
            dropped=dropped,
            words=len(entries),
        )

    def _members(self, group: SuggestedGroup, known: dict[str, KeywordEntry]) -> list[Keyword]:
        """把模型给的写法回查成本批词表里的写法；不在批次内的一律丢弃。"""

        resolved: dict[str, Keyword] = {}
        for term in group.members:
            normalized = normalize_keyword(_strip_label(term))
            if not normalized:
                continue
            key = self.keywords.resolve_key(normalized)
            entry = known.get(key)
            if entry is None:
                continue
            resolved.setdefault(key, Keyword(text=entry.text, kind=entry.kind))
        return list(resolved.values())

    def _target_key(self, target: str, members: list[Keyword]) -> str:
        """目标必须是组内写法；模型给的目标不在组里时取组内第一个。"""

        normalized = normalize_keyword(target)
        if normalized:
            key = self.keywords.resolve_key(normalized)
            if any(item.normalized == key for item in members):
                return key
        return members[0].normalized


def _strip_label(term: str) -> str:
    """去掉模型抄回来的类型标注；没有标注时原样返回。"""

    match = _BATCH_LABEL.match(term.strip())
    return match.group(1) if match else term


def _batch_text(entries: list[KeywordEntry]) -> str:
    """批次词表的提示词文本：每行一个写法，括号里是类型。

    不带频次：合并与否取决于"是不是同一个事物"，频次高低与同指无关，
    写进提示词只会诱导模型按热度分组。
    """

    return "\n".join(
        f"- {item.text}（{KEYWORD_KIND_LABELS.get(item.kind, item.kind.value)}）"
        for item in entries
    )


def _batch_fingerprint(keys: list[str]) -> str:
    """本批词表的指纹：词表内容或切批位置变了，就要重新开始而不是接着用旧偏移。"""

    digest = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()
    return digest[:16]


def _group_fingerprint(keys: list[str]) -> str:
    """一组写法集合的指纹：顺序无关，用于"同一组不重复建议"。"""

    digest = hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()
    return digest[:16]
