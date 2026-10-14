"""合并建议：把词表分批交给模型找同指候选，人工确认后走既有合并流程。

这不是按消息跑的任务：调用发生在请求内（与提示词试跑同类），复用 `LLMFailover` 的
候选调度、Key 冷却与 `llm_calls` 审计。同一批词表只调用一次模型，结果落库供人工处置，
因此"点两次"不会重复付费。

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
    Provider,
    ProviderKey,
    SuggestedGroup,
    SuggestionBatch,
    SuggestResult,
    TaskType,
    normalize_keyword,
)
from rss_v2.domain.prompts import render
from rss_v2.domain.values import new_id, now
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import KeywordRepository, SuggestionRepository
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 单批最大词数：再大就会让一次调用的输出难以核对，也容易超出上下文。
MAX_BATCH = 100

#: 批次文本每行是「- 写法（类型）」；模型常把类型标注一起抄回来。
#: 标注是我们自己渲染上去的，不属于写法，回查前先剥掉。
_BATCH_LABEL = re.compile(r"^(.+?)\s*（(?:实体|主题|事件)）$")

#: 默认批次大小：界面不带参数时用它，批次台账的切分也按它走。
#: 20 是实测值：模型的思考长度随批次增长，60 个词会同时逼近输出上限与 Provider 超时
#: （默认 60 秒）；20 个词实测约 40 秒，留出余量。这个任务不吃批量，吃判断质量。
DEFAULT_BATCH_SIZE = 20


@dataclass(slots=True)
class KeywordSuggestionService:
    """合并建议的生成、列表与处置。"""

    keywords: KeywordRepository  # 词表：切批、别名解析
    suggestions: SuggestionRepository  # 建议与批次台账
    prompts: PromptResolver  # 生效提示词
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与调用审计
    cooldown_seconds: int  # 临时错误冷却秒数

    def generate(self, offset: int | None = None, size: int | None = None) -> SuggestionBatch:
        """生成一批建议；同一批已经生成过就直接回放，不再调用模型。

        offset 省略时接着上一批往后走：界面不需要记住游标，刷新页面也不会从头重复。
        """

        prompt = self.prompts.active(TaskType.KEYWORD_MERGE_SUGGEST.value)
        if prompt is None:
            raise DomainError("prompt_not_configured", "没有可用的合并建议提示词")
        batch_size = max(2, min(size or DEFAULT_BATCH_SIZE, MAX_BATCH))
        start = self._next_offset(batch_size) if offset is None else max(0, offset)
        entries = self.keywords.vocabulary(None, None, 1, None, None, False, batch_size, start)
        total = self.keywords.count_vocabulary(None, None, 1, None, None, False)
        fingerprint = _batch_fingerprint([item.key for item in entries])
        stored = self.suggestions.batch_result(fingerprint, start)
        if stored is not None:
            return SuggestionBatch(
                vocabulary_fingerprint=fingerprint,
                offset=start,
                size=len(entries),
                total=total,
                groups=stored[0],
                dropped=stored[1],
            )
        if not entries:
            return SuggestionBatch(
                vocabulary_fingerprint=fingerprint,
                offset=start,
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
        prompt_version = f"{prompt.version_string}+merge-suggest"

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
            return self._store(fingerprint, start, entries, total, result)

        return self.failover.execute(
            None,
            input_hash(payload.system, payload.user),
            prompt_version,
            self.cooldown_seconds,
            invoke,
            persist,
        )

    def _next_offset(self, size: int) -> int:
        """第一批还没生成过的偏移；全都生成过则返回词表末尾。

        按固定批次大小往后走，因此改批次大小会让游标对不上——界面上批次大小是固定的。
        """

        total = self.keywords.count_vocabulary(None, None, 1, None, None, False)
        start = 0
        while start < total:
            entries = self.keywords.vocabulary(None, None, 1, None, None, False, size, start)
            fingerprint = _batch_fingerprint([item.key for item in entries])
            if self.suggestions.batch_result(fingerprint, start) is None:
                return start
            start += size
        return total

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
        stored = self.suggestions.save_batch(fingerprint, offset, items, dropped, now())
        logger.info("suggestion_batch offset=%d groups=%d dropped=%d", offset, len(stored), dropped)
        return SuggestionBatch(
            vocabulary_fingerprint=fingerprint,
            offset=offset,
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
