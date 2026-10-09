"""RSS v2 的领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from rss_v2.domain.keywords import Keyword


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
    """后台任务类型。"""

    COLLECT_SOURCE = "collect_source"
    TRANSLATE_MESSAGE = "translate_message"
    ENRICH_MESSAGE = "enrich_message"


class LLMProtocol(StrEnum):
    """Provider 使用的模型 API 协议。

    chat_completions 是 OpenAI /v1/chat/completions 形状；anthropic_messages
    是 Anthropic /v1/messages 形状（system 在顶层、认证用 x-api-key）。
    """

    CHAT_COMPLETIONS = "chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"


#: 走 Provider×模型 调度、逐次审计并按冷却重试的任务类型。
LLM_TASK_TYPES = frozenset({TaskType.TRANSLATE_MESSAGE, TaskType.ENRICH_MESSAGE})


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
class Prompt:
    """一条提示词版本；版本不可变，修改等于新建版本。"""

    id: str  # 实体 UUID
    task_kind: str  # 目标任务类型，取值由 domain.prompts.TASK_SPECS 校验
    prompt_key: str  # 稳定业务键，同一键的多条记录构成版本历史
    version: int  # 同一业务键内单调递增的版本号
    name: str  # 显示名
    status: str  # draft 草稿 / active 启用 / archived 归档
    system_template: str  # 系统提示模板，可为空
    user_template: str  # 用户提示模板，不能为空
    note: str  # 版本说明，记录这次改了什么
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒

    @property
    def version_string(self) -> str:
        """审计与幂等键使用的稳定版本串，例如 translation-v1。"""

        return f"{self.prompt_key}-v{self.version}"

    @staticmethod
    def split_version_string(version_string: str) -> tuple[str, int] | None:
        """把 `{prompt_key}-v{version}` 拆回业务键与版本号；格式不符返回 None。

        从右侧按最后一个 `-v` 拆分：业务键本身允许短横线与数字（如 `news-v2`），
        从左拆会把键切错。提示词入库之前的任务快照只带版本串，靠它找回对应版本。
        """

        key, separator, version = version_string.rpartition("-v")
        if not separator or not key or not version.isdigit():
            return None
        return key, int(version)


@dataclass(frozen=True, slots=True)
class PromptUsage:
    """某个提示词版本被引用的次数统计。

    版本串一旦写进审计、结果、任务幂等键或任务分配，改文本就再也无法回溯，
    所以"能否就地编辑或删除"完全由这里的计数决定，而不是由状态决定。
    `used` 由各项计数推导，避免出现"计数非零却标记未使用"的矛盾状态。
    """

    calls: int  # llm_calls 引用次数，含试跑写下的 {版本串}+prompt-test
    results: int  # translations 与 message_enrichments 引用条数
    tasks: int  # 任务的 payload_json 指定该版本的任务数，含已完成的历史任务
    bindings: int  # task_settings 里绑定该版本的来源与分类条目数

    @property
    def used(self) -> bool:
        """是否已被任何审计、结果、任务或分配引用。"""

        return bool(self.calls or self.results or self.tasks or self.bindings)


@dataclass(frozen=True, slots=True)
class TaskSetting:
    """任务分配：某个来源或行业分类对一类任务的覆盖配置。

    enabled 与 prompt_id 为空表示继承上一层，因此"只改开关不动提示词"是自然行为。
    """

    scope: str  # source 来源 / category 行业分类
    scope_id: str  # 来源或分类 UUID
    task_kind: str  # 任务类型
    enabled: bool | None  # 是否自动创建该类任务；空表示继承
    prompt_id: str | None  # 指定提示词；空表示继承
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class TaskSettingView:
    """任务分配的展示视图：原始覆盖值与生效值一起给出。"""

    task_kind: str  # 任务类型
    label: str  # 任务类型显示名
    enabled: bool | None  # 本层设置的开关；空表示继承
    prompt_id: str | None  # 本层指定的提示词；空表示继承
    effective_enabled: bool  # 解析后的开关
    effective_prompt_id: str | None  # 解析后的提示词 UUID
    effective_prompt_version: str | None  # 解析后的提示词版本串
    scope: str  # 生效提示词来自哪一层：source / category / default


@dataclass(frozen=True, slots=True)
class ResolvedTask:
    """解析后的任务配置：该跑哪类任务、用哪条提示词。"""

    task_kind: str  # 任务类型
    enabled: bool  # 是否自动创建任务
    prompt: Prompt | None  # 生效的提示词；没有可用提示词时为空
    scope: str  # 提示词来自哪一层：source / category / default


@dataclass(frozen=True, slots=True)
class PromptSample:
    """提示词编译预览与试跑使用的样例输入。"""

    title: str  # 样例标题
    summary: str  # 样例摘要
    content: str  # 样例正文


@dataclass(frozen=True, slots=True)
class PromptTest:
    """一次提示词试跑的结果；成功带结构化输出，失败带错误分类。"""

    ok: bool  # 是否成功拿到结构化输出
    model: str  # 实际请求的模型标识
    key_masked: str  # 所用密钥掩码；失败且未发起调用时为空
    latency_ms: int  # 调用耗时，毫秒
    total_tokens: int  # 上游返回的总 token 数；未提供为 0
    system: str  # 实际发送的系统提示（渲染后）
    user: str  # 实际发送的用户提示（渲染后）
    output: dict[str, Any]  # 结构化输出字段；失败时为空字典
    error_code: str | None  # 稳定错误分类；成功为空
    error_message: str | None  # 脱敏后的可读错误信息


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
    key_masked: str | None  # 所用密钥的掩码标签，禁止保存密钥值
    model: str | None  # 实际使用的模型标识
    prompt_version: str  # 提示词契约版本
    task_id: str | None  # 关联后台任务 UUID
    error_code: str | None  # 稳定错误分类；成功为空
    error_message: str | None  # 脱敏后的可读错误信息
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class Enrichment:
    """一条消息版本的内容加工结果：精简标题、中文摘要和检索关键词。"""

    id: str  # 实体 UUID
    message_version_id: str  # 加工结果关联的不可变原文版本 UUID
    status: str  # queued/running/succeeded/failed；译文还允许 pending
    title: str | None  # 精简后的标题，过长时压缩
    summary: str | None  # 中文摘要，用于列表展示和检索
    keywords: tuple[Keyword, ...]  # 检索关键词；空元组表示没有可用关键词
    provider_id: str | None  # 模型服务 UUID
    key_masked: str | None  # 所用密钥的掩码标签，禁止保存密钥值
    model: str | None  # 实际使用的模型标识
    prompt_version: str  # 提示词契约版本
    task_id: str | None  # 关联后台任务 UUID
    error_code: str | None  # 稳定错误分类；成功为空
    error_message: str | None  # 脱敏后的可读错误信息
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    """结构化内容加工结果。"""

    title: str  # 精简标题纯文本
    summary: str  # 摘要纯文本
    keywords: tuple[Keyword, ...]  # 检索关键词；展示用 text，匹配用 normalized


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
class SourceDeletion:
    """删除来源的结果统计。"""

    messages: int  # 一并删除的消息条数（版本与译文随消息级联删除）
    unfinished_run_ids: tuple[str, ...]  # 移除该来源后需要重算状态的未完成采集运行


@dataclass(frozen=True, slots=True)
class Task:
    """持久化后台任务。"""

    id: str  # 实体 UUID
    task_type: TaskType  # collect_source 采集 / translate_message 翻译 / enrich_message 内容加工
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
class TaskView:
    """任务列表的展示视图：任务本体 + 它作用于哪条消息或哪个来源。

    目标说明在服务层解析（消息取最新版本标题、采集取来源名），因为任务快照里
    只有 UUID；界面要的是"这条任务在干什么"，不是一串标识。
    """

    task: Task  # 任务本体
    target_kind: str  # message 消息 / source 来源 / unknown 无法解析
    target_id: str  # 目标 UUID；无法解析时为空
    target_label: str  # 目标标题或来源名；无法解析时回落为短标识


@dataclass(frozen=True, slots=True)
class Provider:
    """LLM provider 的非敏感连接配置。"""

    id: str  # 实体 UUID
    name: str  # 显示名称
    base_url: str  # 兼容模型服务地址，无认证信息
    protocol: str  # 请求协议，取值见 LLMProtocol；决定端点、认证头与请求体形状
    enabled: bool  # 是否参与后续处理
    timeout_seconds: float  # 单次模型请求超时秒数
    session_header_name: str | None  # 可选的非敏感会话头名称
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒
    extra_headers: dict[str, str] = field(
        default_factory=lambda: dict[str, str]()
    )  # 兼容协议的非敏感请求头


@dataclass(frozen=True, slots=True)
class ProviderModel:
    """Provider 下的一个模型候选；启停与优先级独立于其它模型。"""

    id: str  # 实体 UUID
    provider_id: str  # 所属模型服务 UUID
    model: str  # 实际请求的模型标识
    enabled: bool  # 是否参与翻译调度
    priority: int  # 数值越小越优先
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class ProviderKey:
    """Provider 下的一枚 API Key；密钥值只存在本机运行目录数据库。"""

    id: str  # 实体 UUID
    provider_id: str  # 模型服务 UUID
    secret: str  # 密钥值；禁止写入日志、审计表和 API 响应
    priority: int  # 数值越小越优先
    enabled: bool  # 是否参与后续处理
    cooldown_until: int | None  # 临时错误冷却截止时间，UTC 秒
    last_status: str | None  # 最近调用状态或错误分类
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class ConnectionTest:
    """一次 Provider×模型连接测试的结果。"""

    ok: bool  # 是否成功完成一次协议探测
    model: str  # 实际测试的模型标识
    key_masked: str  # 所用密钥掩码；失败且未发起调用时为空
    latency_ms: int  # 调用耗时，毫秒；失败时记录最后一次尝试
    total_tokens: int  # 上游返回的总 token 数；未提供为 0
    error_code: str | None  # 稳定错误分类；成功为空
    error_message: str | None  # 脱敏后的可读错误信息
    reply: str = ""  # 模型回复片段（压单行并截断）；失败为空


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
    key_masked: str  # 所用密钥的掩码标签，禁止保存密钥值
    model: str  # 实际使用的模型标识
    prompt_version: str  # 提示词契约版本
    input_hash: str  # 完整输入的 SHA-256；不保存 prompt 原文
    duration_ms: int  # 本次模型调用耗时，毫秒
    token_usage: dict[str, int]  # 输入、输出、总 token 数（若上游提供）
    status: str  # queued/running/succeeded/failed；译文还允许 pending
    error_code: str | None  # 稳定错误分类；成功为空
    created_at: int  # 创建时间，UTC 秒
