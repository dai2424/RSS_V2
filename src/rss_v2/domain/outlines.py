"""提纲的领域对象：版本、分节结构与生成预算。

提纲是"写什么、按什么顺序写、每节依据哪些已核对事实"的契约：模型生成一版，
用户可以基于任意一版改成人工稿（另存新版本，不改写旧行），确认某版之后才具备
生成全文的资格（P5 消费 `digest_run_id` 取当时的事实与引文）。

这一层只有数据与取值约束，不做 IO；越界引用、缺依据与节数下限的判定写在
`outline_rules`，由用例层调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OutlineStatus(StrEnum):
    """提纲版本状态：草稿 / 已确认 / 被后一版取代（确认只作用于一个版本）。"""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class OutlineSource(StrEnum):
    """版本来源：模型生成 / 人工修改保存。人工版本的生成元信息留空。"""

    MODEL = "model"
    HUMAN = "human"


#: 提纲最少节数：少于此数说明模型没有真的分节（例如把整篇写成一节），判为无效输出。
MIN_SECTIONS = 2

#: 小标题与要点的长度上限：提纲是骨架，超长说明模型把正文写进来了。
MAX_HEADING_CHARS = 80
MAX_POINT_CHARS = 300
MAX_ANGLE_CHARS = 500
MAX_TITLE_CHARS = 200

#: 人工保存时每节最多保留几条要点（与模型生成的截断口径一致）。
MAX_POINTS_PER_SECTION = 8


@dataclass(frozen=True, slots=True)
class OutlineSection:
    """提纲的一节：小标题、要点、依据事实与引文、行业影响分析。

    `fact_ids` / `evidence_ids` 已经过服务端校验：只包含本项目已核对且非
    `missing` 的事实，以及属于这些事实的引文；被丢弃的条数记在 `OutlineCounters`。
    """

    heading: str
    talking_points: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    impact_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OutlineCounters:
    """一次提纲生成里被丢弃的引用与结构。

    全部随版本落库并回给界面：丢弃必须看得见，否则"模型引用了不存在的事实"这件事
    只会表现为"提纲看起来短了一点"。
    """

    out_of_range: int = 0  # 引用了不属于本次资料包的事实或引文
    unsupported: int = 0  # 引用了 missing 状态的事实（它自己还没有可用引文）
    dropped_sections: int = 0  # 无小标题或超出节数上限被丢弃的小节
    dropped_points: int = 0  # 超出每节要点上限被丢弃的要点
    without_facts: int = 0  # 保留下来但没有任何事实依据的小节数

    @property
    def has_warnings(self) -> bool:
        """是否存在需要提示用户的丢弃项。"""

        return (
            self.out_of_range > 0
            or self.unsupported > 0
            or self.dropped_sections > 0
            or self.dropped_points > 0
            or self.without_facts > 0
        )


@dataclass(frozen=True, slots=True)
class OutlineVersion:
    """一个提纲版本；只追加，不改写历史行。"""

    id: str
    project_id: str
    version_number: int  # 项目内递增序号；"最新"按它判定
    status: str  # 取值见 OutlineStatus
    source: str  # 取值见 OutlineSource
    title: str
    angle: str
    sections: tuple[OutlineSection, ...]
    material_snapshot_hash: str  # 生成时那个资料包版本的 materials_hash
    digest_run_id: str | None  # 精确绑定生成时的资料整理版本；P5 据此取事实与引文
    prompt_version: str | None  # 模型生成时记录；人工版本为空
    model: str | None  # 模型标识；人工版本为空
    task_id: str | None  # 产出这一版的生成任务；人工版本为空
    based_on_version: str | None  # 人工版本的基准版本
    created_at: int
    confirmed_at: int | None = None
    counters: OutlineCounters = field(default_factory=OutlineCounters)

    @property
    def is_confirmed(self) -> bool:
        """这一版是否处于已确认状态。"""

        return self.status == OutlineStatus.CONFIRMED.value


@dataclass(frozen=True, slots=True)
class OutlineLimits:
    """提纲生成的预算与上限；由配置注入，测试可以传更小的值。"""

    max_input_chars: int = 80000  # 单次调用的输入（选题 + 事实清单）字符上限
    fact_chars: int = 300  # 单条事实陈述放进提示词的字符上限
    max_sections: int = 12  # 分节条数上限；超出的部分丢弃并计数
    max_points: int = 8  # 每节要点条数上限
    max_output_tokens: int = 32768  # 单次调用的输出上限；截断即失败（实测见 settings.py）
