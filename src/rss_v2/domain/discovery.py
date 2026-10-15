"""事件选题发现的领域对象。

一次"发现选题"由三部分组成：**批次**（固定输入范围与输入快照）、**候选**（同一事件的
消息版本集合 + 推荐理由）、**写作项目**（用户选定后的写作上下文）。事件成员复用线索
（`leads` + `lead_messages`）保存消息版本，因此这里不重复定义成员结构。

这一层只有数据与取值约束，不做 IO；边界判定、越界过滤与合并规则写在用例层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DiscoveryStatus(StrEnum):
    """发现批次状态：与任务状态保持同一套取值，便于界面统一展示。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ReasonKind(StrEnum):
    """推荐理由的分类：有趣（新意、故事性）与重要（影响面）。"""

    INTERESTING = "interesting"
    IMPORTANT = "important"


class ProjectStatus(StrEnum):
    """写作项目状态，只前进不回退（由各阶段服务在租约内推进）。"""

    MATERIALS_PENDING = "materials_pending"  # 已选事件，等待资料准备
    MATERIALS_READY = "materials_ready"  # 资料已读取（可能含失败项）
    OUTLINE_READY = "outline_ready"  # 提纲已有草稿
    OUTLINE_CONFIRMED = "outline_confirmed"  # 提纲已确认，可生成全文
    ARTICLE_READY = "article_ready"  # 全文已生成
    SAVED = "saved"  # 有人工保存版本


@dataclass(frozen=True, slots=True)
class DiscoveryInputVersion:
    """发现输入的一条消息（取最新版本），带展示所需的来源与已有关键词。

    `keywords` 只作为模型判断的附加上下文，不作为入选门槛：没有加工结果的消息
    `keywords` 为空，仍然必须进入候选判定。
    """

    message_id: str  # 消息 UUID
    version_id: str  # 该消息的最新版本 UUID，候选成员锁定它
    version_number: int  # 版本号，界面说明"引用的是哪一版"
    title: str  # 版本标题（原文）
    summary: str  # 版本摘要（原文）
    content: str  # 版本正文（原文），可能为空
    url: str  # 原文地址
    published_at: int | None  # 发布时间，UTC 秒；缺失为空
    collected_at: int  # 采集时间，UTC 秒
    source_id: str  # 来源 UUID
    source_name: str  # 来源名
    category_id: str  # 分类 UUID
    category_name: str  # 分类名
    keywords: tuple[str, ...] = ()  # 已加工关键词的原始写法，按重要度排序

    @property
    def event_time(self) -> int:
        """时间口径：发布时间优先，缺失时回落到采集时间。"""

        return self.published_at if self.published_at is not None else self.collected_at


@dataclass(frozen=True, slots=True)
class RecommendationReason:
    """一条推荐理由：分类 + 具体说明（不生成模板套话）。"""

    kind: str  # 取值见 ReasonKind：interesting / important
    text: str  # 具体理由，来自模型输出原文


@dataclass(frozen=True, slots=True)
class DiscoveryBatch:
    """发现批次：输入范围、输入快照与处理结果计数。"""

    id: str  # 批次 UUID
    window_start: int  # 窗口起点，UTC 秒，含
    window_end: int  # 窗口终点，UTC 秒，不含
    category_id: str | None  # 限定分类；为空表示全部分类
    source_id: str | None  # 限定来源；为空表示全部来源
    input_fingerprint: str  # 窗口内输入版本快照指纹
    idempotency_key: str  # 幂等键：范围哈希 + 输入指纹
    status: str  # 取值见 DiscoveryStatus
    task_id: str | None  # 最近一次执行的任务 UUID
    source_label: str  # 展示用范围说明（来源名/分类名/全部订阅）
    message_count: int  # 输入消息条数
    candidate_count: int  # 落库候选数
    duplicate_members: int  # 同一消息被模型放进多个候选的次数
    out_of_range: int  # 模型给出但不在本批输入里的成员数
    dropped_members: int  # 超出成员上限被丢弃的成员数
    dropped_candidates: int  # 超出候选上限被丢弃的候选数
    error_code: str | None  # 失败分类；成功为空
    error_message: str | None  # 失败原因（可读）
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """候选事件：一组消息版本（在 lead_messages 里）+ 推荐信息。"""

    id: str  # 候选 UUID
    batch_id: str  # 所属批次 UUID
    lead_id: str  # 事件成员集合（leads.id），成员固定消息版本
    rank: int  # 批次内展示顺序，从 1 开始
    title: str  # 事件标题（已截断到上限）
    summary: str  # 事件概要（已截断到上限）
    event_time: int | None  # 事件时间，UTC 秒；无法判定为空
    score: int  # 模型自评推荐度 1–5，只用于排序
    reasons: tuple[RecommendationReason, ...]  # 推荐理由，至少一条
    member_count: int  # 成员条数
    dropped_members: int  # 因超出成员上限未纳入的条数
    created_at: int  # 创建时间，UTC 秒


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    """候选被选中后要建立写作项目所需的材料。"""

    batch: DiscoveryBatch  # 候选所在批次
    candidate: DiscoveryCandidate  # 被选中的候选


@dataclass(frozen=True, slots=True)
class WritingProject:
    """写作项目：从一个事件候选出发的写作上下文。"""

    id: str  # 项目 UUID
    lead_id: str  # 事件成员集合 UUID
    candidate_id: str | None  # 来源候选 UUID；非候选来源（后续人工入口）为空
    title: str  # 项目标题
    status: str  # 取值见 ProjectStatus
    created_at: int  # 创建时间，UTC 秒
    updated_at: int  # 最近更新时间，UTC 秒


@dataclass(frozen=True, slots=True)
class DiscoveryScope:
    """一次发现的输入范围（不含输入快照），用于校验与展示。"""

    window_start: int
    window_end: int
    category_id: str | None = None
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryStats:
    """批次处理计数：越界、重复、截断都要看得见，不能静默丢弃。"""

    duplicate_members: int = 0
    out_of_range: int = 0
    dropped_members: int = 0
    dropped_candidates: int = 0

    def merged(self, other: DiscoveryStats) -> DiscoveryStats:
        """跨批累积：每批的计数相加。"""

        return DiscoveryStats(
            duplicate_members=self.duplicate_members + other.duplicate_members,
            out_of_range=self.out_of_range + other.out_of_range,
            dropped_members=self.dropped_members + other.dropped_members,
            dropped_candidates=self.dropped_candidates + other.dropped_candidates,
        )


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    """服务端校验后的候选草稿：成员已解析为版本，计数已统计。"""

    title: str
    summary: str
    event_time: int | None
    score: int
    reasons: tuple[RecommendationReason, ...]
    version_ids: tuple[str, ...]  # 成员消息版本，保持输入时间顺序
    dropped_members: int = 0
    stats: DiscoveryStats = field(default_factory=DiscoveryStats)
