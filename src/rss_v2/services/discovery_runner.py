"""执行一次发现任务：按输入快照分批调用模型，输出候选草稿与计数。

与用例层（`services/discovery.py`）分开：用例层管批次的创建、查询、选题与状态写回，
这里只管"一次执行怎么跑"——读输入快照、渲染提示词、分批调用模型、输出被截断时对半
拆批、把模型输出归一化成候选草稿。模型与提示词版本都取自任务快照，不重新挑选。

调用顺序与方法见 docs/决策记录/2026-10-15-事件选题发现与聚合.md 第 3 节。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from rss_v2.domain import (
    CandidateDraft,
    DiscoveryBatch,
    DiscoveryDigest,
    DiscoveryInputVersion,
    DiscoveryLimits,
    DiscoveryStats,
    DomainError,
    ExternalServiceError,
    Prompt,
    Provider,
    ProviderKey,
    Task,
    TaskType,
)
from rss_v2.domain.discovery_rules import (
    SUMMARY_IN_PROMPT,
    SUMMARY_IN_PROMPT_TIGHT,
    chunk_inputs,
    merge_candidates,
    messages_text,
    normalize_candidates,
    resolve_conflicts,
)
from rss_v2.domain.prompts import render
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.ports import MessageScopeReader
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)

#: 输出被截断时把这一批对半拆开重试的最大层数（1 层 = 拆成两批，2 层 = 最多四批）。
MAX_SPLIT_DEPTH = 2


@dataclass(slots=True)
class DiscoveryRunner:
    """执行单个发现任务；每次模型调用都走 LLMFailover 与调用审计。"""

    scope: MessageScopeReader  # 窗口内消息读取（含无加工结果的消息）
    prompts: PromptResolver  # 任务快照里的提示词版本
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与审计
    cooldown_seconds: int  # 临时错误冷却秒数

    def run(
        self, task: Task, batch: DiscoveryBatch, limits: DiscoveryLimits
    ) -> tuple[list[CandidateDraft], DiscoveryStats]:
        """按任务里的输入快照取数、分批调用模型并归一化，返回候选草稿与计数。"""

        inputs = _task_inputs(task)
        prompt = self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
        current = {
            item.version_id: item
            for item in self.scope.scope_versions(
                batch.window_start,
                batch.window_end,
                batch.source_id,
                batch.category_id,
                limits.max_window_messages,
                0,
            )
        }
        ordered = [current[version_id] for _, version_id in inputs if version_id in current]
        missing = len(inputs) - len(ordered)
        stats = DiscoveryStats(out_of_range=missing)
        if missing:
            logger.warning("discovery_input_missing batch=%s missing=%d", batch.id, missing)
        chunks = chunk_inputs(ordered, max(1, limits.batch_size))
        drafts: list[CandidateDraft] = []
        for index, chunk in enumerate(chunks):
            batch_drafts, batch_stats = self._run_batch(
                task, batch, chunk, index + 1, len(chunks), prompt, limits
            )
            drafts.extend(batch_drafts)
            stats = stats.merged(batch_stats)
        # 先跨批合并（相邻批重叠一条消息，跨切点的事件在这里并回一个候选），
        # 再全局消解：一条消息只能留在一个候选里。
        resolved, conflict_stats = resolve_conflicts(merge_candidates(drafts))
        return resolved, stats.merged(conflict_stats)

    def _run_batch(
        self,
        task: Task,
        batch: DiscoveryBatch,
        chunk: Sequence[DiscoveryInputVersion],
        index: int,
        total: int,
        prompt: Prompt,
        limits: DiscoveryLimits,
        depth: int = 0,
    ) -> tuple[list[CandidateDraft], DiscoveryStats]:
        """调用模型处理一批消息，并把结果归一化为候选草稿。"""

        values = self._values(batch, chunk, index, total, limits)
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
                limits.max_output_tokens,
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            digest: DiscoveryDigest,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> tuple[list[CandidateDraft], DiscoveryStats]:
            return normalize_candidates(digest, chunk, limits.max_members)

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
                task, batch, chunk[: middle + 1], index, total, prompt, limits, depth + 1
            )
            right, right_stats = self._run_batch(
                task, batch, chunk[middle:], index, total, prompt, limits, depth + 1
            )
            return [*left, *right], left_stats.merged(right_stats)

    def _values(
        self,
        batch: DiscoveryBatch,
        chunk: Sequence[DiscoveryInputVersion],
        index: int,
        total: int,
        limits: DiscoveryLimits,
    ) -> dict[str, str]:
        text = messages_text(chunk, SUMMARY_IN_PROMPT, limits.batch_chars)
        if len(text) > limits.batch_chars:
            text = messages_text(chunk, SUMMARY_IN_PROMPT_TIGHT, limits.batch_chars)
            logger.info("discovery_batch_tightened batch=%s chars=%d", batch.id, len(text))
        if len(text) > limits.batch_chars:
            # 仍然超限：硬截断并在日志里留痕；批次计数里能看出这次输入不完整。
            logger.warning("discovery_batch_truncated batch=%s chars=%d", batch.id, len(text))
            text = text[: limits.batch_chars]
        return {
            "window_start": str(batch.window_start),
            "window_end": str(batch.window_end),
            "scope": batch.source_label,
            "messages": text,
            "batch_index": str(index),
            "batch_total": str(total),
            "message_count": str(len(chunk)),
        }


def _task_inputs(task: Task) -> list[tuple[str, str]]:
    """任务里的输入快照：[(消息 id, 版本 id)]，顺序即分批顺序。"""

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
