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
from typing import Any, cast

from rss_v2.domain import (
    KEYWORD_KIND_LABELS,
    DomainError,
    Keyword,
    KeywordEntry,
    KeywordKind,
    MergeSuggestion,
    PinCandidate,
    PinSuggestion,
    PinSuggestionBatch,
    PinSuggestResult,
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

#: 常驻候选的周数与跨周门槛：把最近这些周按 7 天切段，一个词至少跨这几段才算周期话题。
PIN_CANDIDATE_WEEKS = 4
PIN_CANDIDATE_MINIMUM_WEEKS = 3

#: 一次最多排多少批：给一个上限防止误点把队列灌满。
MAX_ENQUEUE = 50

#: 组批时一次读多少词：一次读完再筛掉看过的，避免分批读时"看过的词"把新词挤到后面。
MAX_WORDS = 2000


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
    hot_days: int = 7  # 热词窗口（天）：常驻候选要求本周仍在窗口内

    def enqueue(self, count: int = 1, size: int | None = None) -> SuggestionEnqueue:
        """把还没看过的词排成任务；看过的词不再计入，因此换序也不会重扫。

        每一批的写法随任务快照一起保存：任务执行时只认这份快照，不再回头查词表——
        否则两次执行之间词表变了，模型看到的和记账的就不是同一批词。
        """

        prompt = self._active_prompt()
        batch_size = self._batch_size(size)
        batches = self._scan_batches(max(1, min(count, MAX_ENQUEUE)), batch_size)
        if not batches:
            return SuggestionEnqueue(enqueued=0, queued_words=0)
        candidate = self.failover.first_available()
        if candidate is None:
            raise DomainError("llm_key_not_configured", "没有启用且具有可用 Key 的模型")
        provider, model = candidate
        enqueued = 0
        queued_words = 0
        for offset, fingerprint, entries in batches:
            key = self.idempotency_key(fingerprint)
            queued_words += len(entries)
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
                        "size": len(entries),
                        "fingerprint": fingerprint,
                        "words": [_word_payload(item) for item in entries],
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
            "suggestion_enqueued enqueued=%d offset=%d size=%d",
            enqueued,
            batches[0][0],
            batch_size,
        )
        return SuggestionEnqueue(enqueued=enqueued, queued_words=queued_words)

    def run(self, task: Task) -> SuggestionBatch:
        """执行一个批次任务：按任务快照渲染、调用模型、落库，最后结束任务。

        失败直接抛出，由 worker 记录并决定是否重试；这一批已经生成过（重复入队或重试
        撞上已完成的批次）时直接收尾，不再调用模型。
        """

        batch_offset = int(task.payload.get("offset", 0))
        entries = _payload_words(task)
        prompt = self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
        total = self._total()
        fingerprint = _batch_fingerprint([item.key for item in entries])
        stored = self.suggestions.batch_result(fingerprint, batch_offset)
        if stored is not None:
            self.tasks.complete(task.id, lease_token=task.lease_token)
            return SuggestionBatch(
                vocabulary_fingerprint=fingerprint,
                size=len(entries),
                total=total,
                groups=stored[0],
                dropped=stored[1],
            )
        if not entries:
            self.tasks.complete(task.id, lease_token=task.lease_token)
            return SuggestionBatch(
                vocabulary_fingerprint=fingerprint,
                size=0,
                total=total,
                groups=0,
                dropped=0,
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
            return self._store(fingerprint, batch_offset, entries, total, result)

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
        total = self._total()
        scanned_at = self.suggestions.scanned_at()
        return SuggestionStatus(
            queued=self.tasks.count(status="queued", task_type=kind),
            running=self.tasks.count(status="running", task_type=kind),
            failed=self.tasks.count(status="failed", task_type=kind),
            pending=self.suggestions.pending_count(),
            total=total,
            scanned=len(scanned_at),
            last_scanned_at=max(scanned_at.values()) if scanned_at else None,
        )

    def idempotency_key(self, fingerprint: str) -> str:
        """一批一个任务，键就是这批词的内容指纹：同一批词只排一次。"""

        return f"suggest:{fingerprint}"

    def _batch_size(self, size: int | None) -> int:
        return max(2, min(size or self.batch_size, MAX_BATCH))

    def _total(self) -> int:
        return self.keywords.count_vocabulary(None, None, 1, None, None, False)

    def _active_prompt(self) -> Prompt:
        prompt = self.prompts.active(TaskType.KEYWORD_MERGE_SUGGEST.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的合并建议提示词")
        return prompt

    def _scan_batches(self, count: int, size: int) -> list[tuple[int, str, list[KeywordEntry]]]:
        """按"最久没看过"排队组批，返回 [(起始位置, 指纹, 词表条目)]。

        合并是同指判断，因此把"从没看过的词"与"最久没看过的词"交替排列：每批天然新旧
        混合——新写法不会孤零零地出现（要和老写法放在一起才判断得出是否同指），老词也在
        持续重扫（词表与写法会变，一次看过不等于以后不用再看）。某一侧空了就按另一侧的
        顺序继续，因此第一轮之后自然变成滚动重扫。
        """

        scanned_at = self.suggestions.scanned_at()
        entries = self.keywords.vocabulary(None, None, 1, None, None, False, MAX_WORDS, 0)
        fresh = [item for item in entries if item.key not in scanned_at]
        old = sorted(
            (item for item in entries if item.key in scanned_at),
            key=lambda item: (scanned_at[item.key], item.key),
        )
        queue = _interleave(fresh, old)
        batches: list[tuple[int, str, list[KeywordEntry]]] = []
        for start in range(0, len(queue), size):
            if len(batches) >= count:
                break
            batch = queue[start : start + size]
            # 指纹带上每一词的"上次看过时间"：同一批词重扫是新的一批，
            # 而同一轮里连点两次仍是同一批，不会重复付费。
            fingerprint = _batch_fingerprint(
                [item.key for item in batch],
                [scanned_at.get(item.key, 0) for item in batch],
            )
            if self.tasks.get_by_idempotency(self.idempotency_key(fingerprint)) is None:
                batches.append((start, fingerprint, batch))
        return batches

    def suggest_pins(self, dry_run: bool = False) -> PinSuggestionBatch:
        """从跨周候选里挑出适合常驻的词；同步调用一次，人工确认后才写 pin。

        候选由系统按"最近 4 周里至少 3 周出现、且本周仍在窗口内"算出（可复算、可解释），
        模型只做挑选。候选为空时直接返回——常见于样本还不够的冷启动。
        """

        timestamp = now()
        since = timestamp - self.hot_days * 86400
        candidates = self.keywords.pin_candidates(
            timestamp, since, PIN_CANDIDATE_WEEKS, PIN_CANDIDATE_MINIMUM_WEEKS
        )
        if dry_run or not candidates:
            return PinSuggestionBatch(candidates=len(candidates), picks=0, dropped=0)
        prompt = self.prompts.active(TaskType.KEYWORD_PIN_SUGGEST.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的常驻建议提示词")
        values = {"vocabulary": _candidate_text(candidates)}
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )
        prompt_version = prompt.version_string

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[PinSuggestResult, dict[str, int], int]:
            return self.provider.suggest_pins(provider, model_name, secret, payload, prompt_version)

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: PinSuggestResult,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> PinSuggestionBatch:
            return self._store_pins(candidates, result)

        return self.failover.execute(
            None,
            input_hash(payload.system, payload.user),
            prompt_version,
            self.cooldown_seconds,
            invoke,
            persist,
        )

    def pin_suggestions(
        self, status: str | None = "pending", limit: int = 50
    ) -> list[PinSuggestion]:
        """常驻建议列表；默认只看待处理的。"""

        return self.keywords.pin_suggestions(status, limit)

    def resolve_pin(self, key: str, accepted: bool) -> str:
        """采纳（写入常驻表）或忽略一条常驻建议。"""

        return self.keywords.resolve_pin_suggestion(
            key, "accepted" if accepted else "dismissed", now()
        )

    def _store_pins(
        self, candidates: list[PinCandidate], result: PinSuggestResult
    ) -> PinSuggestionBatch:
        """把模型挑出的写法折算成建议：不在候选里、已建议过的都丢弃。"""

        known = {item.key: item for item in candidates}
        items: list[PinSuggestion] = []
        seen: set[str] = set()
        dropped = 0
        for pick in result.picks:
            normalized = normalize_keyword(_strip_label(pick.term))
            key = self.keywords.resolve_key(normalized) if normalized else ""
            candidate = known.get(key)
            if candidate is None or key in seen:
                dropped += 1
                logger.info("pin_suggestion_dropped term=%s", pick.term)
                continue
            seen.add(key)
            items.append(
                PinSuggestion(
                    key=key,
                    text=candidate.text,
                    rationale=pick.rationale.strip(),
                    weeks=candidate.weeks,
                    status="pending",
                    created_at=now(),
                    resolved_at=None,
                )
            )
        stored = self.keywords.save_pin_suggestions(items, now())
        logger.info(
            "pin_suggestion_batch candidates=%d picks=%d dropped=%d",
            len(candidates),
            len(stored),
            dropped,
        )
        return PinSuggestionBatch(candidates=len(candidates), picks=len(stored), dropped=dropped)

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
            fingerprint,
            offset,
            items,
            dropped,
            [item.key for item in entries],
            now(),
        )
        logger.info("suggestion_batch offset=%d groups=%d dropped=%d", offset, len(stored), dropped)
        return SuggestionBatch(
            vocabulary_fingerprint=fingerprint,
            size=len(entries),
            total=total,
            groups=len(stored),
            dropped=dropped,
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


def _word_payload(item: KeywordEntry) -> dict[str, str]:
    """任务快照里的一个词：匹配键、展示写法与类型，执行时不再回头查词表。"""

    return {"key": item.key, "text": item.text, "kind": item.kind.value}


def _payload_words(task: Task) -> list[KeywordEntry]:
    """从任务快照读回这批词；缺字段的条目跳过，不让一个坏快照打断整批。"""

    raw = task.payload.get("words")
    if not isinstance(raw, list):
        return []
    entries: list[KeywordEntry] = []
    for item in cast(list[object], raw):
        entry = cast(dict[str, Any], item) if isinstance(item, dict) else {}
        key = str(entry.get("key") or "")
        text = str(entry.get("text") or "")
        if not key or not text:
            continue
        try:
            kind = KeywordKind(str(entry.get("kind") or "topic"))
        except ValueError:
            kind = KeywordKind.TOPIC
        entries.append(
            KeywordEntry(
                key=key,
                text=text,
                kind=kind,
                mentions=0,
                sources=0,
                first_seen_at=0,
                last_seen_at=0,
                aliases=(),
            )
        )
    return entries


def _interleave(fresh: list[KeywordEntry], old: list[KeywordEntry]) -> list[KeywordEntry]:
    """新词与旧词交替排列：每批都能同时看到"没见过的写法"和"见过但很久没看的写法"。"""

    queue: list[KeywordEntry] = []
    for index in range(max(len(fresh), len(old))):
        if index < len(fresh):
            queue.append(fresh[index])
        if index < len(old):
            queue.append(old[index])
    return queue


def _candidate_text(candidates: list[PinCandidate]) -> str:
    """候选的提示词文本：每行一个写法，括号里是它在最近几周里跨了几周。"""

    return "\n".join(f"- {item.text}（跨 {item.weeks} 周）" for item in candidates)


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


def _batch_fingerprint(keys: list[str], scanned: list[int] | None = None) -> str:
    """一批词的指纹：内容相同、这些词的"上次看过时间"也相同才算同一批。

    带上时间维度是为了让滚动重扫能生成新任务——否则同一批词重扫会被当成重复入队而跳过。
    """

    seed = "\n".join(keys)
    if scanned is not None:
        seed += "\n" + "\n".join(str(value) for value in scanned)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _group_fingerprint(keys: list[str]) -> str:
    """一组写法集合的指纹：顺序无关，用于"同一组不重复建议"。"""

    digest = hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()
    return digest[:16]
