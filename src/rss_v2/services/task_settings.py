"""任务分配用例：某来源或分类要跑哪些任务、用哪条提示词。"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.domain import DomainError, TaskSetting, TaskSettingView
from rss_v2.domain.prompts import TASK_SPECS, spec_for
from rss_v2.domain.values import now
from rss_v2.ports import TaskSettingRepository
from rss_v2.services.prompt_resolver import PromptResolver

#: 允许配置的作用域：来源覆盖分类，分类覆盖全局默认。
SCOPES = ("source", "category")


@dataclass(frozen=True, slots=True)
class TaskSettingService:
    """任务分配的读写与生效值解析。"""

    settings: TaskSettingRepository  # 覆盖配置
    prompts: PromptResolver  # 解析生效提示词

    def describe(self, scope: str, scope_id: str) -> list[TaskSettingView]:
        """每类任务一行：本层覆盖值与解析后的生效值。"""

        self._check_scope(scope)
        stored = {item.task_kind: item for item in self.settings.list_for(scope, scope_id)}
        views: list[TaskSettingView] = []
        for spec in TASK_SPECS.values():
            setting = stored.get(spec.kind.value)
            resolved = self.prompts.effective(scope, scope_id, spec.kind.value)
            views.append(
                TaskSettingView(
                    task_kind=spec.kind.value,
                    label=spec.label,
                    enabled=setting.enabled if setting else None,
                    prompt_id=setting.prompt_id if setting else None,
                    effective_enabled=resolved.enabled,
                    effective_prompt_id=resolved.prompt.id if resolved.prompt else None,
                    effective_prompt_version=(
                        resolved.prompt.version_string if resolved.prompt else None
                    ),
                    scope=resolved.scope,
                )
            )
        return views

    def replace(self, scope: str, scope_id: str, items: list[TaskSetting]) -> list[TaskSettingView]:
        """整组覆盖：空值表示回到继承；未列出的任务类型不保留覆盖。"""

        self._check_scope(scope)
        timestamp = now()
        rows: list[TaskSetting] = []
        for item in items:
            spec_for(item.task_kind)
            if item.prompt_id is not None:
                prompt = self.prompts.prompts.get(item.prompt_id)
                if prompt is None:
                    raise DomainError("prompt_not_found", "指定的提示词不存在")
                if prompt.task_kind != item.task_kind:
                    raise DomainError("prompt_kind_mismatch", "提示词与任务类型不匹配")
            if item.enabled is None and item.prompt_id is None:
                continue
            rows.append(
                TaskSetting(
                    scope=scope,
                    scope_id=scope_id,
                    task_kind=item.task_kind,
                    enabled=item.enabled,
                    prompt_id=item.prompt_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        self.settings.replace(rows)
        return self.describe(scope, scope_id)

    def _check_scope(self, scope: str) -> None:
        if scope not in SCOPES:
            raise DomainError("scope_invalid", "作用域只能是 source 或 category")
