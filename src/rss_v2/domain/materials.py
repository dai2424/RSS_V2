"""资料准备的领域对象：搜索命中、原文读取结果与资料行。

资料（material）是写作项目的输入证据快照：RSS 资料固定到消息版本，外部资料保存
URL、发布者、发布时间、获取时间、正文与内容指纹。读取失败的候选同样落成资料行
（status=failed + 错误码），这样"没读到什么、为什么没读到"在界面上看得见，
符合"超限提示或分批，不静默丢材料"的口径。

错误码取自[检索与网页读取](../../../docs/决策记录/2026-10-15-检索与网页读取.md)，
用常量而不是散落的字面量，避免同一种失败出现多个写法。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaterialKind(StrEnum):
    """资料类型：RSS 原文（固定消息版本）与联网补充（外部网页）。"""

    RSS = "rss"
    WEB = "web"


class MaterialStatus(StrEnum):
    """资料状态：读到正文 / 只有摘要 / 来源不可用 / 读取失败。"""

    READ = "read"  # 读到正文
    SUMMARY_ONLY = "summary_only"  # 只有摘要或页面描述
    UNAVAILABLE = "unavailable"  # 来源已下架（404/410 等）
    FAILED = "failed"  # 超时、网络、体积超限、无法解析、预算跳过等


#: 检索失败分类（ExternalServiceError 的 code，可重试的进冷却重试）。
SEARCH_UNCONFIGURED = "search_unconfigured"
SEARCH_NETWORK_ERROR = "search_network_error"
SEARCH_TIMEOUT = "search_timeout"
SEARCH_FORMAT_DISABLED = "search_format_disabled"
SEARCH_RATE_LIMITED = "search_rate_limited"
SEARCH_SERVER_ERROR = "search_server_error"
SEARCH_INVALID_OUTPUT = "search_invalid_output"

#: 原文读取失败分类（写进资料行，不失败整个任务）。
ARTICLE_TIMEOUT = "article_timeout"
ARTICLE_HTTP_ERROR = "article_http_error"
ARTICLE_TOO_LARGE = "article_too_large"
ARTICLE_UNPARSABLE = "article_unparsable"
ARTICLE_INVALID_URL = "article_invalid_url"
SSRF_BLOCKED = "ssrf_blocked"

#: 预算耗尽：候选留着但这一轮不读，界面按"未读取"呈现而不是当作失败。
BUDGET_EXHAUSTED = "budget_exhausted"

#: 提示词渲染里每条消息的摘要长度上限。
MEMBER_SUMMARY_LIMIT = 200

#: 正文短于这个长度时只能算"仅摘要"（可能是付费墙、导航壳或反爬页）。
MIN_ARTICLE_CHARS = 400


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一条搜索命中：只说明"这里可能有资料"，读取成功才算资料。"""

    title: str
    url: str  # 规范化前的原始 URL
    snippet: str  # 搜索摘要；只作线索提示，不进入引用
    engine: str  # 命中的引擎名，用于解释来源覆盖
    published_at: int | None = None  # 可用的发布时间，UTC 秒；缺失为空


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """一次检索的结果：命中列表 + 未响应的引擎。

    两者必须分开呈现：部分引擎被上游限流时结果可能为空，那不是"没有资料"，
    而是"这次没问到"。资料摘要会把未响应引擎记下来。
    """

    hits: tuple[SearchHit, ...]
    unresponsive_engines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArticleRead:
    """一次原文读取的结果；失败也返回对象，由调用方落成资料行。"""

    status: str  # 取值见 MaterialStatus
    final_url: str  # 跟随重定向后的最终地址；失败时为空
    title: str  # 页面标题（og:title → h1 → <title>）
    publisher: str  # 发布者（og:site_name → 域名）
    published_at: int | None  # 页面给出的发布时间，UTC 秒；缺失为空
    text: str  # 正文纯文本（仅摘要时为摘要文本）
    content_hash: str  # 规范化文本的 SHA-256；失败时为空
    byte_size: int  # 实际读取字节数
    truncated: bool = False  # 命中体积上限被截断
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class Material:
    """资料行：项目的一条证据快照。"""

    id: str  # 资料 UUID
    project_id: str  # 所属写作项目
    kind: str  # 取值见 MaterialKind
    status: str  # 取值见 MaterialStatus
    url: str  # 原始地址（RSS 资料为消息版本的链接）
    final_url: str  # 跟随重定向后的地址；为空时等同 url
    title: str
    publisher: str
    published_at: int | None  # 原始发布时间，UTC 秒；历史背景可以早于发现窗口
    fetched_at: int  # 获取时间，UTC 秒
    content_text: str  # 正文快照；未读到正文时为空
    content_hash: str  # 规范化文本指纹；未读到为空
    byte_size: int
    truncated: bool
    message_version_id: str | None  # RSS 资料固定到的消息版本；外部资料为空
    task_id: str | None  # 产生这条资料的任务
    error_code: str
    error_message: str
    created_at: int


@dataclass(frozen=True, slots=True)
class MaterialLimits:
    """资料准备的预算与上限；由配置注入，测试可以传更小的值。"""

    max_queries: int = 3  # 每个项目最多几次检索（中文、英文、官方站点各一）
    search_result_limit: int = 8  # 每次检索取多少条命中（SearXNG 没有 limit 参数，本地截断）
    max_reads: int = 12  # 每个项目最多读取几份原文（含 RSS 缺正文时的回读）
    max_bytes: int = 2000000  # 单次读取的字节上限
    read_timeout_seconds: float = 20.0  # 单次读取超时
    search_timeout_seconds: float = 20.0  # 单次检索超时


@dataclass(frozen=True, slots=True)
class MaterialQuery:
    """一条检索式：文本 + 用途说明（便于人工复核为什么搜它）。"""

    text: str
    purpose: str = ""


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    """一条检索式的执行结果：命中数、未响应引擎与（可选）失败原因。"""

    query: str
    purpose: str = ""
    hits: int = 0
    unresponsive_engines: tuple[str, ...] = ()
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class MaterialSummary:
    """一次资料准备的摘要；存到项目上，界面据此显示范围与缺口。"""

    queries: tuple[QueryOutcome, ...] = ()
    read: int = 0  # 读到正文
    summary_only: int = 0  # 只有摘要
    unavailable: int = 0  # 来源不可用
    failed: int = 0  # 读取失败
    duplicates: int = 0  # 与已读资料重复而跳过的数量
    skipped_by_budget: int = 0  # 预算耗尽未读取的候选数
    notes: tuple[str, ...] = ()
    finished_at: int = 0  # 完成时间，UTC 秒

    def merged(self, other: MaterialSummary) -> MaterialSummary:
        """累加两次运行的结果（重试/重跑时保留已有计数）。"""

        return MaterialSummary(
            queries=(*self.queries, *other.queries),
            read=self.read + other.read,
            summary_only=self.summary_only + other.summary_only,
            unavailable=self.unavailable + other.unavailable,
            failed=self.failed + other.failed,
            duplicates=self.duplicates + other.duplicates,
            skipped_by_budget=self.skipped_by_budget + other.skipped_by_budget,
            notes=(*self.notes, *other.notes),
            finished_at=other.finished_at or self.finished_at,
        )
