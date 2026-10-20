"""全文的领域对象：版本、正文结构、引用快照与生成预算。

全文是"按已确认提纲写出来的成稿"：模型生成一版 `generated`+`draft`，P6a 的人工
编辑另存 `edited`+`saved` 新版本（P5 只写前一种）。正文的权威结构是 `ArticleBody`
（分节段落 + 引用表 + 来源列表），`body_markdown` 由它派生，不单独维护。

**引用与来源都带快照**：每条引用存它的事实陈述、状态与引文原文，来源列表存
标题/发布者/链接。这样资料包被清理、事实被重跑都不会让旧稿的依据断裂——验收要求
"源消息更新后旧稿引用与证据不变"。

这一层只有数据与取值约束，不做 IO；引用校验、来源推导与降级规则写在
`article_rules`，HTML/Markdown 渲染写在 `article_markdown`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ArticleKind(StrEnum):
    """版本来源：模型生成 / 人工保存（P6a）。"""

    GENERATED = "generated"
    EDITED = "edited"


class ArticleStatus(StrEnum):
    """版本状态：草稿 / 已保存。P5 只产生草稿，P6a 的人工稿落为已保存。"""

    DRAFT = "draft"
    SAVED = "saved"


class ParagraphKind(StrEnum):
    """段落类型：客观事实 / 分析 / 推测（与 `project_facts.kind` 同一套取值）。"""

    FACT = "fact"
    ANALYSIS = "analysis"
    SPECULATION = "speculation"


#: 全文至少几节：为零说明模型没写正文；提纲的 2 节在成稿里允许合并，不设更高下限。
MIN_SECTIONS = 1

#: 结构上限：超出的部分丢弃并计数，不静默。
MAX_TITLE_CHARS = 200
MAX_LEAD_CHARS = 500
MAX_HEADING_CHARS = 80
MAX_PARAGRAPH_CHARS = 2000
MAX_SECTIONS = 12
MAX_SOURCES = 30

#: 引用标记形态：正文写 `[^1]`，`citations[].marker` 存 `"1"`。
#: 用脚注形态而不是 `[1]`，避免与序号、数学式、Markdown 链接前缀撞车。
MARKER_PATTERN = r"\[\^(\d{1,3})\]"


@dataclass(frozen=True, slots=True)
class ArticleParagraph:
    """正文的一段：文本 + 类型。`fact` 段必须带至少一个引用标记。"""

    text: str
    kind: str  # 取值见 ParagraphKind；已由服务端归一化


@dataclass(frozen=True, slots=True)
class ArticleSection:
    """正文的一节：小标题 + 若干段落。"""

    heading: str
    paragraphs: tuple[ArticleParagraph, ...] = ()


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    """一条引文的快照：原文、来源资料与定位偏移。

    随版本落库，界面（Sheet）与预览直接读快照，不再依赖当时的资料包是否还在。
    """

    evidence_id: str
    fact_id: str
    text: str
    material_id: str
    material_title: str
    material_url: str
    locator: int = -1  # 引文在资料正文中的字符偏移；-1 表示未知


@dataclass(frozen=True, slots=True)
class ArticleCitation:
    """正文里一个引用标记对应的事实与引文快照。"""

    marker: str  # "1"、"2"……；正文里写 `[^1]`
    fact_id: str
    fact_kind: str  # 被引用事实的类型（fact/analysis/speculation），仅展示
    fact_status: str  # 生成时的状态（supported/conflicting/missing 之一）
    statement: str  # 事实陈述的快照
    quotes: tuple[QuoteSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class ArticleSource:
    """文末来源列表的一项；全部字段来自项目资料行，不接受模型自造。"""

    material_id: str
    title: str
    publisher: str
    url: str


@dataclass(frozen=True, slots=True)
class ArticleBody:
    """正文的权威结构；`body_markdown` 由它派生。"""

    sections: tuple[ArticleSection, ...]
    citations: tuple[ArticleCitation, ...] = ()  # 按 marker 升序
    source_list: tuple[ArticleSource, ...] = ()


@dataclass(frozen=True, slots=True)
class ArticleCounters:
    """一次生成里被丢弃/降级的条目。

    只有成功生成才写版本行，所以这里只承载"生成了但有东西被丢掉"的计数；
    校验失败（悬空标记、事实段缺引用、没有正文）不落版本，原因写任务错误信息。
    """

    out_of_range: int = 0  # 引用了不属于本次资料包的事实或引文
    unsupported: int = 0  # 引用了 missing 状态的事实（它自己还没有可用引文）
    unused_citations: int = 0  # 引用表里有、正文没用到
    duplicate_markers: int = 0  # 同一标记在引用表里重复出现
    invalid_markers: int = 0  # 标记不是 `[^n]` 形态
    downgraded_speculation: int = 0  # 推测段缺不确定表述，降级为分析
    unknown_kind: int = 0  # 段落类型不在 fact/analysis/speculation 之内
    rejected_links: int = 0  # 非 http/https 的链接被还原成纯文字
    dropped_paragraphs: int = 0  # 空段落
    dropped_sections: int = 0  # 无小标题或超出节数上限的小节
    invalid_sources: int = 0  # 模型给的来源列表里匹配不到已读资料
    unused_sources: int = 0  # 匹配到了但正文没引用
    added_sources: int = 0  # 正文用到、模型漏列，由服务端补上
    dropped_sources: int = 0  # 超出来源列表上限

    @property
    def has_warnings(self) -> bool:
        """是否存在需要提示用户的丢弃/降级项。"""

        return (
            self.out_of_range > 0
            or self.unsupported > 0
            or self.unused_citations > 0
            or self.duplicate_markers > 0
            or self.invalid_markers > 0
            or self.downgraded_speculation > 0
            or self.unknown_kind > 0
            or self.rejected_links > 0
            or self.dropped_paragraphs > 0
            or self.dropped_sections > 0
            or self.invalid_sources > 0
            or self.unused_sources > 0
            or self.added_sources > 0
            or self.dropped_sources > 0
        )


@dataclass(frozen=True, slots=True)
class ArticleVersion:
    """一个全文版本；只追加，不改写历史行。"""

    id: str
    project_id: str
    version_number: int  # 项目内递增序号；"最新"按它判定
    kind: str  # 取值见 ArticleKind
    status: str  # 取值见 ArticleStatus
    title: str
    lead: str
    body: ArticleBody
    body_markdown: str  # 由 body 派生（服务端生成，读取与导出直接使用）
    outline_version_id: str  # 生成时使用的已确认提纲版本
    material_snapshot_hash: str  # 等于该提纲版本的快照哈希
    digest_run_id: str | None  # 提纲绑定的资料整理版本
    prompt_version: str | None  # 模型生成时记录；人工版本为空
    model: str | None  # 模型标识；人工版本为空
    task_id: str | None  # 产出这一版的生成任务；人工版本为空
    base_article_version_id: str | None  # P6a 人工稿的基准版本
    created_at: int
    saved_at: int | None = None
    counters: ArticleCounters = field(default_factory=ArticleCounters)

    @property
    def source_count(self) -> int:
        """文末来源数量；为 1 时界面要提示"单一来源，不得暗示多方共识"。"""

        return len(self.body.source_list)


@dataclass(frozen=True, slots=True)
class ArticleView:
    """文章列表视图：版本 + 项目级当前状态。"""

    versions: tuple[ArticleVersion, ...]
    latest_version_id: str | None
    saved_version_id: str | None  # 最新的人工保存版本（P6a 之后才会出现）
    outline_version_id: str | None  # 当前确认的提纲版本
    has_unconfirmed_changes: bool  # 已确认的提纲不是最新版本（提示要重新确认）
    blocking_count: int  # 未处理的阻塞缺口（只标记，不拦生成）
    task_status: str | None  # 最近一次生成任务的状态（失败也要显示）

    @property
    def has_blocking(self) -> bool:
        """项目里是否还有未处理的阻塞缺口。"""

        return self.blocking_count > 0


@dataclass(frozen=True, slots=True)
class ArticleLimits:
    """全文生成的预算与上限；由配置注入，测试可以传更小的值。"""

    max_input_chars: int = 80000  # 单次调用的输入（提纲 + 事实清单 + 待办）字符上限
    fact_chars: int = 300  # 单条事实陈述放进提示词的字符上限
    max_sections: int = MAX_SECTIONS  # 分节条数上限；超出的丢弃并计数
    max_paragraph_chars: int = MAX_PARAGRAPH_CHARS  # 单段字符上限
    max_output_tokens: int = 32768  # 单次调用的输出上限；截断即失败（实测见 settings.py）
