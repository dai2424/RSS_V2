"""任务配置与提示词的唯一解析点。

解析顺序是"来源 → 行业分类 → 全局默认"：每层只覆盖显式设置的字段，空字段继续
向上取值，因此"来源只关掉某类任务、提示词仍用分类默认"是自然行为。
"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.domain import DomainError, MessageVersion, Prompt, ResolvedTask
from rss_v2.domain.prompts import prompt_values, render
from rss_v2.llm import PromptPayload
from rss_v2.ports import PromptRepository, SourceRepository, TaskSettingRepository


@dataclass(frozen=True, slots=True)
class PromptResolver:
    """按来源与分类配置解析生效提示词。"""

    prompts: PromptRepository  # 提示词版本
    settings: TaskSettingRepository  # 来源与分类的任务分配
    sources: SourceRepository  # 取来源所属分类

    def resolve(self, source_id: str, task_kind: str) -> ResolvedTask:
        """解析某来源某类任务的生效配置；来源缺失时只用全局默认。"""

        source = self.sources.get(source_id)
        layers = (("source", source.id), ("category", source.category_id)) if source else ()
        return self.resolve_layers(layers, task_kind)

    def resolve_scope(self, scope: str, scope_id: str, task_kind: str) -> ResolvedTask:
        """只按某一层解析，用于分类默认的界面展示。"""

        return self.resolve_layers(((scope, scope_id),), task_kind)

    def effective(self, scope: str, scope_id: str, task_kind: str) -> ResolvedTask:
        """某作用域下真实生效的配置。

        来源作用域会继续看它所属分类，因此界面能显示"继承自分类"还是"本层覆盖"；
        分类作用域只看自己与全局默认。
        """

        if scope == "source":
            return self.resolve(scope_id, task_kind)
        return self.resolve_scope(scope, scope_id, task_kind)

    def resolve_layers(self, layers: tuple[tuple[str, str], ...], task_kind: str) -> ResolvedTask:
        """逐层取值：每层只覆盖显式设置的字段，空字段继续向上。"""

        enabled: bool | None = None
        prompt: Prompt | None = None
        scope = "default"
        for layer, scope_id in layers:
            setting = self.settings.get(layer, scope_id, task_kind)
            if setting is None:
                continue
            if enabled is None and setting.enabled is not None:
                enabled = setting.enabled
            if prompt is None and setting.prompt_id is not None:
                prompt = self.prompts.get(setting.prompt_id)
                if prompt is not None:
                    scope = layer
        if prompt is None:
            prompt = self.prompts.active_for(task_kind)
        return ResolvedTask(
            task_kind=task_kind,
            enabled=True if enabled is None else enabled,
            prompt=prompt,
            scope=scope,
        )

    def active(self, task_kind: str) -> Prompt | None:
        """全局默认提示词；用于连接测试等没有来源上下文的场景。"""

        return self.prompts.active_for(task_kind)

    def snapshot(self, prompt_id: str, version_string: str = "") -> Prompt:
        """按任务快照读取提示词；归档版本仍可执行，被删除则明确失败。

        提示词入库之前的任务快照只有 `prompt_version`、没有 `prompt_id`（入库前
        提示词硬编码在适配器里）。这类任务用版本串找回同一条记录，升级后仍能执行、
        也能重试；两者都对不上才判定提示词真的不存在。
        """

        prompt = self.prompts.get(prompt_id) if prompt_id else None
        if prompt is None and version_string:
            prompt = self.prompts.by_version_string(version_string)
        if prompt is None:
            raise DomainError("prompt_missing", "任务快照的提示词已不存在，请重新创建任务")
        return prompt

    def payload(self, prompt: Prompt, version: MessageVersion) -> PromptPayload:
        """把模板渲染成最终提示词。"""

        values = prompt_values(version.title, version.summary, version.content)
        return PromptPayload(
            system=render(prompt.system_template, values),
            user=render(prompt.user_template, values),
        )
