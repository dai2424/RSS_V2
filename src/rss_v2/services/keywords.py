"""关键词索引维护、相关消息与存量回填。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rss_v2.domain import DomainError, RelatedMessage, Task
from rss_v2.ports import KeywordRepository


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
