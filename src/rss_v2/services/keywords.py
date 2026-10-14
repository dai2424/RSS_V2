"""关键词索引维护、相关消息、存量回填与人工合并。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from rss_v2.domain import (
    DomainError,
    KeywordEntry,
    KeywordOverview,
    MergePreview,
    MergeRecord,
    RelatedMessage,
    Task,
    normalize_keyword,
)
from rss_v2.domain.values import now
from rss_v2.ports import KeywordRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """存量回填的预览或执行结果。"""

    candidates: int  # 命中的候选消息数，已受 limit 限制
    enqueued: int  # 新建或复用的加工任务数
    skipped: int  # 因缺少提示词或可用 Key 跳过的条数
    reasons: tuple[str, ...]  # 去重后的跳过原因，供界面直接展示


@dataclass(slots=True)
class KeywordService:
    """关键词索引与回填用例。"""

    keywords: KeywordRepository  # 关键词索引、检索与候选查询
    enqueue: Callable[[str], Task]  # 单条加工入队，复用提示词解析与幂等规则
    hot_days: int = 7  # 热词窗口（天）
    hot_min_mentions: int = 5  # 热词门槛（窗口内出现次数）

    def rebuild_index(self) -> int:
        """按已存的加工结果重建关键词索引；本地操作，不调用模型。"""

        return self.keywords.rebuild_index()

    def related(self, message_id: str, limit: int = 8) -> list[RelatedMessage]:
        """与指定消息共享关键词的其他消息。"""

        return self.keywords.related_messages(message_id, limit)

    def backfill(self, source_id: str | None, limit: int, dry_run: bool) -> BackfillResult:
        """为没有加工结果的存量消息入队。

        预览与执行走同一条候选查询，先看条数再决定跑不跑：回填是人工触发、可限量的动作，
        因此不套用自动入队的长度阈值。跳过条目不中断整批——缺提示词或缺 Key 时，
        其余条目仍然应该入队。
        """

        candidates = self.keywords.messages_missing_enrichment(source_id, limit)
        if dry_run:
            return BackfillResult(len(candidates), 0, 0, ())
        enqueued = 0
        skipped = 0
        reasons: dict[str, None] = {}
        for message_id in candidates:
            try:
                self.enqueue(message_id)
                enqueued += 1
            except DomainError as exc:
                skipped += 1
                reasons.setdefault(exc.message, None)
        return BackfillResult(len(candidates), enqueued, skipped, tuple(reasons))

    def vocabulary(
        self,
        query: str | None,
        kind: str | None,
        min_count: int | None,
        since: int | None,
        until: int | None,
        pinned_only: bool,
        limit: int,
        offset: int,
        scope: str = "hot",
    ) -> tuple[list[KeywordEntry], int]:
        """词表一页与总数；两者必须用同一套筛选条件，否则分页会对不上。

        默认口径是"热词"：近 hot_days 天、出现次数达到门槛。显式传 since/until/min_count
        可以覆盖它；scope="all" 表示看全量存量（历史词也看得见，用于回看与合并）。
        """

        effective_since, effective_until, effective_min = self._scope(
            scope, min_count, since, until
        )
        items = self.keywords.vocabulary(
            query,
            kind,
            effective_min,
            effective_since,
            effective_until,
            pinned_only,
            limit,
            offset,
        )
        total = self.keywords.count_vocabulary(
            query, kind, effective_min, effective_since, effective_until, pinned_only
        )
        return items, total

    def _scope(
        self, scope: str, min_count: int | None, since: int | None, until: int | None
    ) -> tuple[int | None, int | None, int]:
        """把口径折算成实际筛选条件；显式参数优先于口径默认值。"""

        if scope not in {"hot", "all"}:
            raise DomainError("keyword_scope_invalid", "口径只能是 hot 或 all")
        default_since = now() - self.hot_days * 86400 if scope == "hot" else None
        # 全量口径沿用历史默认"折叠孤词"，热词口径用门槛值。
        default_min = self.hot_min_mentions if scope == "hot" else 2
        return (
            since if since is not None else default_since,
            until,
            max(1, min_count if min_count is not None else default_min),
        )

    def set_pin(self, key: str, pinned: bool) -> str:
        """标记或取消常驻词，返回规范化后的词键。

        键统一走归一化：界面传回的词表键与手工输入的写法都能用，且与合并、检索
        使用同一套匹配键。
        """

        normalized = normalize_keyword(key)
        if not normalized:
            raise DomainError("keyword_pin_empty", "常驻词的键不能为空")
        self.keywords.set_pin(normalized, pinned, now())
        return normalized

    def overview(self, days: int = 14, top_sources: int = 10) -> KeywordOverview:
        """概览与分布；主指标按热词口径，存量另给两个全量数字。"""

        return self.keywords.overview(days, top_sources, self.hot_days, self.hot_min_mentions)

    def merge_preview(self, keys: list[str], target: str) -> MergePreview:
        """合并影响面；输入统一归一化，界面传回的词表键与手工输入都能用。"""

        sources = self._sources(keys, target)
        return self.keywords.merge_preview(sources, normalize_keyword(target))

    def merge(self, keys: list[str], target: str) -> MergeRecord:
        """合并同指写法并留下可撤销的记录。

        只处理同指（OpenAI / openai / Open AI），不处理上下位（尊界 与 尊界V800）——
        后者会抹平检索粒度，那是分组而不是合并该管的事。
        """

        sources = self._sources(keys, target)
        record = self.keywords.merge(sources, normalize_keyword(target))
        logger.info(
            "keyword_merge id=%s target=%s merged=%s messages=%d",
            record.id,
            record.target_key,
            len(sources),
            record.messages,
        )
        return record

    def undo_merge(self, merge_id: str) -> int:
        """撤销一次合并，别名按原值回填。"""

        restored = self.keywords.undo_merge(merge_id)
        logger.info("keyword_merge_undo id=%s restored=%d", merge_id, restored)
        return restored

    def merges(self, limit: int = 20) -> list[MergeRecord]:
        """最近的合并记录。"""

        return self.keywords.merges(limit)

    def _sources(self, keys: list[str], target: str) -> list[str]:
        """把待合并键归一化并解析到规范词：输入别名时合并它所属的词条，而不是别名本身。"""

        target_key = normalize_keyword(target)
        if not target_key:
            raise DomainError("keyword_merge_empty", "目标词不能为空")
        sources: list[str] = []
        for key in keys:
            normalized = self.keywords.resolve_key(normalize_keyword(key))
            if normalized and normalized != target_key and normalized not in sources:
                sources.append(normalized)
        if not sources:
            raise DomainError("keyword_merge_empty", "至少选择一个与目标不同的写法")
        return sources
