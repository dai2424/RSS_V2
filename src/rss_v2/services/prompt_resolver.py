"""任务配置与提示词的唯一解析点。

解析顺序是"来源 → 行业分类 → 全局默认"：每层只覆盖显式设置的字段，空字段继续
向上取值，因此"来源只关掉某类任务、提示词仍用分类默认"是自然行为。

渲染也在这里：占位符取值、词表注入与最终拼装只有一份实现，界面上的编译预览、
提示词试跑与真实加工走的是同一条路。
"""

from __future__ import annotations

from dataclasses import dataclass

from rss_v2.domain import (
    DomainError,
    MessageVersion,
    Prompt,
    PromptVocabulary,
    ResolvedTask,
    render_vocabulary,
)
from rss_v2.domain.prompts import placeholders, prompt_values, render
from rss_v2.domain.values import now
from rss_v2.llm import PromptPayload
from rss_v2.ports import (
    KeywordRepository,
    PromptRepository,
    SourceRepository,
    TaskSettingRepository,
)

#: 注入词表的占位符名；模板引用了它才去查词表，翻译这类任务不受影响。
VOCABULARY_PLACEHOLDER = "vocabulary"


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """渲染后的提示词与本次注入的词表。

    vocabulary 供调用方判定复用与记录词表版本；为 None 表示这次没有注入
    （模板没引用占位符，或词表注入被关闭）。
    """

    payload: PromptPayload  # 最终发给模型的 system/user
    vocabulary: PromptVocabulary | None  # 本次注入的词表快照


@dataclass(frozen=True, slots=True)
class PromptResolver:
    """按来源与分类配置解析生效提示词。"""

    prompts: PromptRepository  # 提示词版本
    settings: TaskSettingRepository  # 来源与分类的任务分配
    sources: SourceRepository  # 取来源所属分类
    keywords: KeywordRepository | None = None  # 注入词表的数据源；为空表示不注入
    vocabulary_limit: int = 0  # 注入条数上限，0 表示关闭注入
    vocabulary_recent_days: int = 7  # "近期新增"一段的时间窗（天）

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

    def payload(self, prompt: Prompt, version: MessageVersion) -> RenderedPrompt:
        """把模板渲染成最终提示词，并带上本次注入的词表快照。"""

        templates = (prompt.system_template, prompt.user_template)
        values, vocabulary = self._values(
            templates, version.title, version.summary, version.content
        )
        return RenderedPrompt(
            payload=PromptPayload(
                system=render(prompt.system_template, values),
                user=render(prompt.user_template, values),
            ),
            vocabulary=vocabulary,
        )

    def values_for_templates(
        self, templates: tuple[str, ...], title: str, summary: str, content: str
    ) -> dict[str, str]:
        """编译与试跑用的占位符取值；与执行期共用同一套注入规则。

        预览必须看到模型实际会看到的东西，因此这里注入的是当前真实词表，
        而不是样例词表。
        """

        values, _ = self._values(templates, title, summary, content)
        return values

    def record_version(self, vocabulary: PromptVocabulary | None) -> int | None:
        """记录这次注入的词表版本，返回版本号；没有注入时返回 None。

        只有加工执行调这里：编译预览与试跑共用注入规则但不记录，否则在界面上点一次
        预览就会把版本推进一步，让所有存量结果看起来"早于当前词表版本"。
        """

        if vocabulary is None or self.keywords is None:
            return None
        return self.keywords.note_vocabulary(vocabulary.fingerprint, now())

    def _values(
        self, templates: tuple[str, ...], title: str, summary: str, content: str
    ) -> tuple[dict[str, str], PromptVocabulary | None]:
        values = prompt_values(title, summary, content)
        vocabulary = self._injection(templates)
        if vocabulary is not None:
            values[VOCABULARY_PLACEHOLDER] = render_vocabulary(vocabulary)
        return values, vocabulary

    def _injection(self, templates: tuple[str, ...]) -> PromptVocabulary | None:
        """模板引用 {{vocabulary}} 时取当前注入词表，否则不查库。

        注入关闭（limit 为 0）或没有可用的词表数据源时返回 None：调用方据此
        保持"没有词表可判定"的状态，而不是把所有关键词都算成新建。
        """

        if self.keywords is None or self.vocabulary_limit <= 0:
            return None
        if not any(VOCABULARY_PLACEHOLDER in placeholders(template) for template in templates):
            return None
        return self.keywords.prompt_vocabulary(
            self.vocabulary_limit, self.vocabulary_recent_days, now()
        )
