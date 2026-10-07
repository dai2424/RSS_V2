"""RSS v2 的领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceLanguage(StrEnum):
    """来源声明语言。"""

    AUTO = "auto"
    ENGLISH = "en"
    CHINESE = "zh"
    MIXED = "mixed"


class TaskStatus(StrEnum):
    """任务生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskType(StrEnum):
    """第一版支持的任务类型。"""

    COLLECT_SOURCE = "collect_source"
    TRANSLATE_MESSAGE = "translate_message"


@dataclass(frozen=True, slots=True)
class Category:
    """行业分类。"""

    id: str  # 实体 UUID
    name: str  # 显示名称
    slug: str  # 稳定分类标识
    is_builtin: bool  # 是否内置分类
    is_active: bool  # 是否可被新来源选择
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class Source:
    """RSS 来源基本配置。"""

    id: str  # 实体 UUID
    name: str  # 显示名称
    url: str  # HTTP(S) 原文或来源地址
    platform: str  # 来源所属平台
    language: SourceLanguage  # auto 自动识别 / en 英文 / zh 中文 / mixed 混合
    category_id: str  # 所属行业分类 UUID
    enabled: bool  # 是否参与后续处理
    time_offset_minutes: int  # 对解析后发布时间追加的分钟偏移
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒
    feed_title: str | None = None  # Feed 声明标题
    feed_link: str | None = None  # Feed 声明链接
    feed_description: str | None = None  # Feed 描述纯文本
    feed_language: str | None = None  # Feed 声明语言
    feed_author: str | None = None  # Feed 作者或组织
    feed_updated_at: str | None = None  # Feed 原始更新时间文本（元数据）
    source_type: str = "rss"  # RSS / Atom 的解析类型
    metadata: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())  # 非标准 Feed 元数据


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """一次来源健康检查结果。"""

    id: str  # 实体 UUID
    source_id: str  # RSS 来源 UUID
    checked_at: int  # 健康检查时间，UTC 秒
    http_status: int | None  # 实际 HTTP 状态；网络失败为空
    latency_ms: int | None  # 请求与解析耗时，毫秒
    parse_success: bool  # RSS 解析是否成功
    entry_count: int  # 解析后的条目数量
    error_code: str | None = None  # 稳定错误分类；成功为空
    error_message: str | None = None  # 脱敏后的可读错误信息


@dataclass(frozen=True, slots=True)
class FeedItem:
    """解析后的 RSS 条目。"""

    external_id: str  # 来源中的条目标识，缺失时使用稳定回退
    title: str  # 标题纯文本
    summary: str  # 摘要纯文本
    content: str  # 正文纯文本
    url: str  # HTTP(S) 原文或来源地址
    published_at: int | None  # 发布时间，UTC 秒；缺失为空
    language: SourceLanguage  # auto 自动识别 / en 英文 / zh 中文 / mixed 混合
    content_hash: str  # 标题、摘要、正文的 SHA-256


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    """一次 RSS 请求的 feed 元数据和条目。"""

    title: str | None  # 标题纯文本
    link: str | None  # Feed 主页链接
    description: str | None  # Feed 描述
    language: str | None  # auto 自动识别 / en 英文 / zh 中文 / mixed 混合
    author: str | None  # Feed 作者
    updated_at: str | None  # 最近更新时间，UTC 秒
    metadata: dict[str, Any]  # 非标准 Feed 元数据
    items: list[FeedItem]  # 本次解析条目


@dataclass(frozen=True, slots=True)
class Message:
    """消息逻辑实体。"""

    id: str  # 实体 UUID
    source_id: str  # RSS 来源 UUID
    external_id: str  # 来源中的条目标识，缺失时使用稳定回退
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class MessageVersion:
    """不可变的消息内容版本。"""

    id: str  # 实体 UUID
    message_id: str  # 逻辑消息 UUID
    version_number: int  # 消息内单调递增版本号
    title: str  # 标题纯文本
    summary: str  # 摘要纯文本
    content: str  # 正文纯文本
    url: str  # HTTP(S) 原文或来源地址
    published_at: int | None  # 发布时间，UTC 秒；缺失为空
    collected_at: int  # 采集时间，UTC 秒
    language: SourceLanguage  # auto 自动识别 / en 英文 / zh 中文 / mixed 混合
    content_hash: str  # 标题、摘要、正文的 SHA-256


@dataclass(frozen=True, slots=True)
class Translation:
    """一条消息版本的中文翻译。"""

    id: str  # 实体 UUID
    message_version_id: str  # 译文关联的不可变原文版本 UUID
    status: str  # queued/running/succeeded/failed；译文还允许 pending
    title: str | None  # 标题纯文本
    summary: str | None  # 摘要纯文本
    content: str | None  # 正文纯文本
    provider_id: str | None  # 模型服务 UUID
    key_ref: str | None  # 外部环境变量的密钥引用，禁止保存密钥值
    model: str | None  # 实际使用的模型标识
    prompt_version: str  # 提示词契约版本
    task_id: str | None  # 关联后台任务 UUID
    error_code: str | None  # 稳定错误分类；成功为空
    error_message: str | None  # 脱敏后的可读错误信息
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class CollectionRun:
    """一次采集运行的汇总。"""

    id: str  # 实体 UUID
    source_ids: list[str]  # 本次采集来源 UUID 列表
    status: TaskStatus  # queued/running/succeeded/failed；译文还允许 pending
    requested_at: int  # 采集请求时间，UTC 秒
    completed_at: int | None  # 最终完成时间，UTC 秒；重试中为空
    created_count: int = 0  # 新增消息数量
    updated_count: int = 0  # 新增消息版本数量
    skipped_count: int = 0  # 未变化而跳过的条目数量
    failed_count: int = 0  # 失败来源数量


@dataclass(frozen=True, slots=True)
class Task:
    """持久化后台任务。"""

    id: str  # 实体 UUID
    task_type: TaskType  # collect_source 采集 / translate_message 翻译
    idempotency_key: str  # 避免重复创建任务的业务键
    status: TaskStatus  # queued/running/succeeded/failed；译文还允许 pending
    attempts: int  # 已领取执行的尝试次数
    lease_until: int | None  # 当前租约截止时间，UTC 秒
    input_version_id: str | None  # 任务绑定的输入版本 UUID
    output_version_id: str | None  # 成功译文 UUID（采集为空）
    payload: dict[str, Any]  # 非敏感任务参数快照
    error_code: str | None  # 稳定错误分类；成功为空
    error_message: str | None  # 脱敏后的可读错误信息
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒
    lease_token: str | None = None  # 当前领取者随机 token，用于隔离过期 worker
    available_at: int = 0  # 最早可重试时间，UTC 秒


@dataclass(frozen=True, slots=True)
class Provider:
    """LLM provider 的非敏感配置。"""

    id: str  # 实体 UUID
    name: str  # 显示名称
    base_url: str  # 兼容模型服务地址，无认证信息
    model: str  # 实际使用的模型标识
    enabled: bool  # 是否参与后续处理
    priority: int  # 数值越小越优先
    timeout_seconds: float  # 单次模型请求超时秒数
    session_header_name: str | None  # 可选的非敏感会话头名称
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒
    extra_headers: dict[str, str] = field(
        default_factory=lambda: dict[str, str]()
    )  # 兼容协议的非敏感请求头


@dataclass(frozen=True, slots=True)
class ProviderKey:
    """Provider 下的密钥引用，不包含密钥值。"""

    id: str  # 实体 UUID
    provider_id: str  # 模型服务 UUID
    key_ref: str  # 外部环境变量的密钥引用，禁止保存密钥值
    priority: int  # 数值越小越优先
    enabled: bool  # 是否参与后续处理
    cooldown_until: int | None  # 临时错误冷却截止时间，UTC 秒
    last_status: str | None  # 最近调用状态或错误分类
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """结构化翻译结果。"""

    title: str  # 标题纯文本
    summary: str  # 摘要纯文本
    content: str  # 正文纯文本


@dataclass(frozen=True, slots=True)
class LLMCall:
    """模型调用审计信息。"""

    id: str  # 实体 UUID
    task_id: str | None  # 关联后台任务 UUID
    provider_id: str  # 模型服务 UUID
    key_ref: str  # 外部环境变量的密钥引用，禁止保存密钥值
    model: str  # 实际使用的模型标识
    prompt_version: str  # 提示词契约版本
    input_hash: str  # 完整输入的 SHA-256；不保存 prompt 原文
    duration_ms: int  # 本次模型调用耗时，毫秒
    token_usage: dict[str, int]  # 输入、输出、总 token 数（若上游提供）
    status: str  # queued/running/succeeded/failed；译文还允许 pending
    error_code: str | None  # 稳定错误分类；成功为空
    created_at: int  # 创建时间，UTC 秒
