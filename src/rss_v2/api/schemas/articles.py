"""全文的响应模型。

列表只回版本摘要（正文可能有几十 KB，20 个版本全带上会让列表响应过大）；
单版本详情才带分节正文与 Markdown；引用证据只在预览里返回。
"""

from __future__ import annotations

from pydantic import BaseModel


class ArticleParagraphResponse(BaseModel):
    """正文的一段：文本 + 类型。"""

    text: str
    kind: str  # fact / analysis / speculation


class ArticleSectionResponse(BaseModel):
    """正文的一节。"""

    heading: str
    paragraphs: list[ArticleParagraphResponse]


class ArticleSourceResponse(BaseModel):
    """文末来源列表的一项；全部字段来自项目资料行。"""

    material_id: str
    title: str
    publisher: str
    url: str


class ArticleCountersResponse(BaseModel):
    """生成时被丢弃/降级的条目：全部回给界面，丢弃必须看得见。"""

    out_of_range: int  # 引用了不属于本次资料包的事实或引文
    unsupported: int  # 引用了缺引文（missing）的事实
    unused_citations: int  # 引用表里有、正文没用到
    duplicate_markers: int  # 同一标记在引用表里重复出现
    invalid_markers: int  # 标记不是 [^n] 形态
    downgraded_speculation: int  # 推测段缺不确定表述，降级为分析
    unknown_kind: int  # 段落类型不在 fact/analysis/speculation 之内
    rejected_links: int  # 非 http/https 的链接被还原成纯文字
    dropped_paragraphs: int  # 空段落
    dropped_sections: int  # 无小标题或超出节数上限的小节
    invalid_sources: int  # 模型给的来源匹配不到已读资料
    unused_sources: int  # 匹配到了但正文没引用
    added_sources: int  # 正文用到、模型漏列，由服务端补上
    dropped_sources: int  # 超出来源列表上限


class ArticleSummaryResponse(BaseModel):
    """版本摘要：列表用，不含正文。"""

    id: str
    version_number: int  # 项目内递增序号；"最新"按它判定
    kind: str  # generated（模型生成）/ edited（人工保存）
    status: str  # draft / saved
    title: str
    outline_version_id: str  # 生成时使用的已确认提纲版本
    prompt_version: str | None  # 模型生成时记录；人工版本为空
    model: str | None  # 模型标识；人工版本为空
    task_id: str | None  # 产出这一版的生成任务；人工版本为空
    source_count: int  # 文末来源数量；为 1 时界面提示"单一来源"
    counters: ArticleCountersResponse  # 生成时的丢弃/降级计数
    created_at: int
    saved_at: int | None
    base_article_version_id: str | None  # 人工版本的基准版本
    is_latest: bool


class ArticleVersionResponse(ArticleSummaryResponse):
    """单个版本：摘要字段 + 正文结构 + Markdown。"""

    lead: str
    sections: list[ArticleSectionResponse]
    source_list: list[ArticleSourceResponse]
    body_markdown: str  # 由正文结构派生，供复制与导出（P6b）
    material_snapshot_hash: str
    digest_run_id: str | None


class ArticleListResponse(BaseModel):
    """版本列表与项目级当前状态（新到旧）。"""

    items: list[ArticleSummaryResponse]
    latest_version_id: str | None
    saved_version_id: str | None  # 最新的人工保存版本（P6a 之后才会出现）
    outline_version_id: str | None  # 当前确认的提纲版本
    has_unconfirmed_changes: bool  # 已确认的提纲不是最新版本
    has_blocking: bool  # 仍有未处理的阻塞缺口（只标记，不拦生成）
    blocking_count: int
    task_status: str | None  # 最近一次生成任务的状态；没有生成过为 null


class ArticleQuoteResponse(BaseModel):
    """一条引文：原文、来源资料与链接（预览里的证据 Sheet 用）。"""

    text: str
    locator: int  # 引文在资料正文中的字符偏移；-1 表示未知
    material_id: str
    material_title: str
    material_url: str


class ArticleCitationResponse(BaseModel):
    """一个引用标记对应的证据。"""

    marker: str  # 正文里写作 [^marker]
    fact_id: str
    fact_kind: str  # 被引用事实的类型
    fact_status: str  # 生成时的核对结论（supported/conflicting/missing）
    statement: str
    quotes: list[ArticleQuoteResponse]


class ArticleNoteResponse(BaseModel):
    """一条未处理的缺口/分歧。"""

    kind: str  # gap / conflict / note
    text: str
    blocking: bool


class ArticlePreviewResponse(BaseModel):
    """预览：受控 HTML + 引用证据 + 待核对项。"""

    version: ArticleSummaryResponse
    title: str
    lead: str
    html: str  # 服务端渲染的受控 HTML（输入全部转义，只含白名单标签）
    citations: list[ArticleCitationResponse]
    pending_notes: list[ArticleNoteResponse]
    source_count: int
    single_source: bool  # 只有一条来源：界面提示不得暗示多方共识
    has_blocking: bool
    blocking_count: int
    task_status: str | None
