"""提示词模板、任务规格与编译校验。

这一层是纯逻辑，不访问数据库也不调用模型：界面上的"编译"、执行前的渲染和
占位符校验都以这里的任务规格为准。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from rss_v2.domain.errors import DomainError
from rss_v2.domain.models import TaskType

#: 模板占位符语法：{{变量名}}，允许花括号内出现空格。
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """一类模型任务的规格：编译校验与界面提示都以此为准。"""

    kind: TaskType  # 任务类型
    label: str  # 界面显示名
    variables: tuple[str, ...]  # 模板可以使用的占位符
    required_variables: tuple[str, ...]  # 建议引用的输入占位符，缺失只警告
    output_fields: tuple[str, ...]  # 必须声明的结构化输出字段，缺失即编译错误
    assignable: bool = True  # 能否分配给来源/分类；只用于提示词的类型标为 False


#: 已实现的模型任务。新增任务类型时在这里加一条规格，并补齐执行与结果存储。
TASK_SPECS: dict[TaskType, TaskSpec] = {
    TaskType.TRANSLATE_MESSAGE: TaskSpec(
        kind=TaskType.TRANSLATE_MESSAGE,
        label="翻译成中文",
        variables=("title", "summary", "content"),
        required_variables=("title", "content"),
        output_fields=("title", "summary", "content"),
    ),
    TaskType.ENRICH_MESSAGE: TaskSpec(
        kind=TaskType.ENRICH_MESSAGE,
        label="内容加工",
        variables=("title", "summary", "content", "vocabulary"),
        required_variables=("title", "content"),
        output_fields=("title", "summary", "keywords"),
    ),
    TaskType.KEYWORD_PIN_SUGGEST: TaskSpec(
        kind=TaskType.KEYWORD_PIN_SUGGEST,
        label="常驻建议",
        variables=("vocabulary",),
        required_variables=("vocabulary",),
        output_fields=("picks",),
        # 候选由系统按跨周出现算出、一次同步调用即可，因此不分配给来源也不进队列。
        assignable=False,
    ),
    TaskType.KEYWORD_MERGE_SUGGEST: TaskSpec(
        kind=TaskType.KEYWORD_MERGE_SUGGEST,
        label="合并建议",
        variables=("vocabulary",),
        required_variables=("vocabulary",),
        output_fields=("groups",),
        # 不是按来源、按消息跑的任务：由词表页按钮触发，因此不进任务分配界面。
        assignable=False,
    ),
    TaskType.DISCOVER_EVENTS: TaskSpec(
        kind=TaskType.DISCOVER_EVENTS,
        label="选题发现",
        variables=(
            "window_start",
            "window_end",
            "scope",
            "messages",
            "batch_index",
            "batch_total",
            "message_count",
        ),
        required_variables=("messages",),
        output_fields=("candidates",),
        # 输入是跨来源的时间窗口，按来源/分类分配没有意义：直接用当前生效版本。
        assignable=False,
    ),
    TaskType.PREPARE_MATERIALS: TaskSpec(
        kind=TaskType.PREPARE_MATERIALS,
        label="资料检索式",
        variables=("title", "summary", "members"),
        required_variables=("title", "members"),
        output_fields=("queries",),
        # 输入是某个选题，按来源分配没有意义：直接用当前生效版本。
        assignable=False,
    ),
    TaskType.DIGEST_MATERIALS: TaskSpec(
        kind=TaskType.DIGEST_MATERIALS,
        label="资料整理",
        variables=("title", "summary", "materials"),
        required_variables=("title", "materials"),
        output_fields=("facts", "conflicts", "gaps"),
        # 输入是某个选题的资料包，按来源分配没有意义：直接用当前生效版本。
        assignable=False,
    ),
}

#: 编译预览使用的样例取值；没有真实消息时也能看到渲染结果。
#: vocabulary 只是兜底文案——编译与试跑都会带上当前真实词表（见 PromptResolver）。
SAMPLE_VALUES: dict[str, str] = {
    "title": "示例标题",
    "summary": "示例摘要。",
    "content": "示例正文。",
    "vocabulary": "实体：ChatGPT、OpenAI\n主题：AI 编程助手",
    # 选题发现的编译预览：真实取值由服务层按窗口内的消息渲染。
    "window_start": "1791337493",
    "window_end": "1791942293",
    "scope": "全部订阅",
    "messages": "[示例消息ID] 2026-10-08 10:41 | 示例来源 | 示例标题 | 示例摘要 | 关键词：示例",
    "batch_index": "1",
    "batch_total": "1",
    "message_count": "1",
    # 资料准备的编译预览：真实取值由服务层按项目成员渲染。
    "members": "1. 尊界 V800 制动测试引争议（来源：风向旗参考快讯，2026-10-08 10:41 上海）",
    # 资料整理的编译预览：真实取值由服务层按已读资料渲染。
    "materials": "[材料 material-1] 懂车帝实测报道（量子位）\n正文：示例正文。",
}


def spec_for(kind: str) -> TaskSpec:
    """按任务类型取规格；未注册的类型明确报错，不做静默回退。"""

    try:
        parsed = TaskType(kind)
    except ValueError as exc:
        raise DomainError("task_kind_unknown", f"未知任务类型：{kind}") from exc
    spec = TASK_SPECS.get(parsed)
    if spec is None:
        raise DomainError("task_kind_unsupported", f"任务类型尚未支持提示词：{kind}")
    return spec


def placeholders(template: str) -> tuple[str, ...]:
    """模板中出现的占位符，按首次出现顺序去重。"""

    seen: dict[str, None] = {}
    for match in PLACEHOLDER.finditer(template):
        seen.setdefault(match.group(1), None)
    return tuple(seen)


def render(template: str, values: Mapping[str, str]) -> str:
    """替换模板占位符。

    未知占位符必须报错而不是原样保留：把 `{{xxx}}` 发给模型会得到无法解释的结果。
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise DomainError("prompt_placeholder_unknown", f"未知占位符：{{{{{name}}}}}")
        return values[name]

    return PLACEHOLDER.sub(replace, template)


def prompt_values(
    title: str, summary: str, content: str, vocabulary: str | None = None
) -> dict[str, str]:
    """执行任务时的占位符取值；只有引用词表的任务才需要传 vocabulary。"""

    values = {"title": title, "summary": summary, "content": content}
    if vocabulary is not None:
        values["vocabulary"] = vocabulary
    return values


@dataclass(frozen=True, slots=True)
class CompileIssue:
    """一条编译问题；错误阻止保存，警告只提示。"""

    field: str  # 相关字段：system_template / user_template / template
    message: str  # 面向使用者的说明


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """编译结果：校验问题与样例渲染结果。"""

    ok: bool  # 没有错误时为真
    errors: tuple[CompileIssue, ...]  # 阻止保存的问题
    warnings: tuple[CompileIssue, ...]  # 只提示的问题
    system: str  # 样例渲染后的系统提示
    user: str  # 样例渲染后的用户提示


def compile_prompt(
    kind: str,
    system_template: str,
    user_template: str,
    sample: Mapping[str, str] | None = None,
) -> CompiledPrompt:
    """校验模板并渲染预览。

    错误：用户提示为空、出现规格之外的占位符、没有声明该任务的结构化输出字段。
    输出字段按错误处理，因为模型漏给字段只会让结果静默降级。
    警告：没有引用建议的输入占位符。
    """

    spec = spec_for(kind)
    errors: list[CompileIssue] = []
    warnings: list[CompileIssue] = []
    if not user_template.strip():
        errors.append(CompileIssue("user_template", "用户提示不能为空"))
    for field, template in (
        ("system_template", system_template),
        ("user_template", user_template),
    ):
        for name in placeholders(template):
            if name not in spec.variables:
                errors.append(
                    CompileIssue(
                        field,
                        f"未知占位符 {{{{{name}}}}}，可用：{'、'.join(spec.variables)}",
                    )
                )
    combined = f"{system_template}\n{user_template}"
    for output in spec.output_fields:
        if output not in combined:
            errors.append(CompileIssue("template", f"模板没有声明输出字段 {output}"))
    for name in spec.required_variables:
        if f"{{{{{name}}}}}" not in combined:
            warnings.append(
                CompileIssue("template", f"模板没有引用 {{{{{name}}}}}，模型可能缺少输入")
            )

    if errors:
        # 有错误就不渲染：未知占位符会让渲染失败，而这里要的是问题清单。
        return CompiledPrompt(
            ok=False, errors=tuple(errors), warnings=tuple(warnings), system="", user=""
        )
    values = {**SAMPLE_VALUES, **(sample or {})}
    return CompiledPrompt(
        ok=True,
        errors=(),
        warnings=tuple(warnings),
        system=render(system_template, values),
        user=render(user_template, values),
    )
