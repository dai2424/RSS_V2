"""一次全文生成的执行器：渲染提示词 → 调用模型 → 输出结构化全文草稿。

与用例层（`services/articles.py`）分开：用例层管任务入队、引用校验、版本落库与项目
状态，这里只管"一次执行怎么跑"。模型与提示词版本都取自任务快照，不重新挑选——
任务排队期间换了提示词或模型，不会改变这次运行的输入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rss_v2.domain import (
    ArticleDraft,
    ArticleLimits,
    Prompt,
    Provider,
    ProviderKey,
    Task,
    TaskType,
)
from rss_v2.domain.prompts import render
from rss_v2.llm import LLMProvider, PromptPayload
from rss_v2.services.llm_failover import LLMFailover, input_hash
from rss_v2.services.prompt_resolver import PromptResolver

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ArticleRunner:
    """执行单个全文生成任务；模型调用走 LLMFailover 与调用审计。"""

    prompts: PromptResolver  # 任务快照里的提示词版本
    provider: LLMProvider  # 唯一模型调用协议
    failover: LLMFailover  # 候选回退、Key 冷却与审计
    cooldown_seconds: int  # Key 冷却时长（秒）
    limits: ArticleLimits = ArticleLimits()

    def run(
        self, task: Task, *, title: str, summary: str, outline: str, facts: str, notes: str
    ) -> ArticleDraft:
        """调用模型生成全文草稿；结构由 `ArticleDraft` 校验，业务规则由调用方过滤。

        全文与提纲一样不做分批：一次生成只调用一次模型。输出被截断
        （`llm_output_truncated`）直接失败——重试仍会撞同一个上限，需要的是提高
        `article_max_output_tokens` 或改提示词，而不是悄悄截成半篇。
        """

        prompt = self._prompt(task)
        values = {
            "title": title,
            "summary": summary,
            "outline": outline,
            "facts": facts,
            "notes": notes,
        }
        payload = PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )

        def invoke(
            provider: Provider, model_name: str, secret: str
        ) -> tuple[ArticleDraft, dict[str, int], int]:
            return self.provider.structured(
                provider,
                model_name,
                secret,
                payload,
                prompt.version_string,
                TaskType.GENERATE_ARTICLE.value,
                ArticleDraft,
                self.limits.max_output_tokens,
            )

        def persist(
            provider: Provider,
            model_name: str,
            key: ProviderKey,
            result: ArticleDraft,
            tokens: dict[str, int],
            duration_ms: int,
        ) -> ArticleDraft:
            return result

        return self.failover.execute(
            task.id,
            input_hash(payload.system, payload.user),
            prompt.version_string,
            self.cooldown_seconds,
            invoke,
            persist,
        )

    def _prompt(self, task: Task) -> Prompt:
        return self.prompts.snapshot(
            str(task.payload.get("prompt_id", "")), str(task.payload.get("prompt_version", ""))
        )
