"""提纲的请求与响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class OutlineSectionResponse(BaseModel):
    """提纲的一节：小标题、要点、依据事实与引文、行业影响分析。"""

    heading: str
    talking_points: list[str]
    fact_ids: list[str]  # 本项目已核对事实的 id；引用已在校验时过滤
    evidence_ids: list[str]  # 属于所选事实的引文 id
    impact_notes: list[str]


class OutlineCountersResponse(BaseModel):
    """生成时被丢弃的引用与结构：全部回给界面，丢弃必须看得见。"""

    out_of_range: int  # 引用了不属于本次资料包的事实或引文
    unsupported: int  # 引用了缺引文（missing）的事实
    dropped_sections: int  # 无小标题或超出节数上限被丢弃的小节
    dropped_points: int  # 超出每节要点上限被丢弃的要点
    without_facts: int  # 保留下来但没有任何事实依据的小节数


class OutlineVersionResponse(BaseModel):
    """一个提纲版本。"""

    id: str
    version_number: int  # 项目内递增序号；"最新"按它判定
    status: str  # draft / confirmed / superseded
    source: str  # model（生成）/ human（人工保存）
    title: str
    angle: str
    sections: list[OutlineSectionResponse]
    counters: OutlineCountersResponse  # 生成时的丢弃计数；人工版本为零值
    material_snapshot_hash: str  # 生成时资料包版本的指纹
    digest_run_id: str | None  # 生成时用的资料整理版本（编辑器据此取事实与引文）
    prompt_version: str | None  # 模型生成时记录；人工版本为空
    model: str | None  # 模型标识；人工版本为空
    task_id: str | None  # 产出这一版的生成任务；人工版本为空
    based_on_version: str | None  # 人工版本的基准版本
    created_at: int
    confirmed_at: int | None
    is_latest: bool  # 是否项目内最新版本（非最新不能确认）


class OutlineListResponse(BaseModel):
    """提纲版本列表与项目级当前状态（新到旧）。"""

    items: list[OutlineVersionResponse]
    latest_version_id: str | None
    confirmed_version_id: str | None
    has_unconfirmed_changes: bool  # 已有确认版本，但最新版本不是它
    has_blocking: bool  # 仍有未处理的阻塞缺口（只标记，不拦生成与确认）
    blocking_count: int
    task_status: str | None  # 最近一次生成任务的状态；没有生成过为 null


class OutlineResponse(BaseModel):
    """保存或确认之后的版本与项目级状态。"""

    version: OutlineVersionResponse
    latest_version_id: str | None
    confirmed_version_id: str | None
    has_unconfirmed_changes: bool
    has_blocking: bool
    blocking_count: int
    task_status: str | None


class OutlineSectionRequest(BaseModel):
    """人工保存提交的一节；字段与模型输出一致，走同一条校验。"""

    heading: str = ""
    talking_points: list[str] = []
    fact_ids: list[str] = []
    evidence_ids: list[str] = []
    impact_notes: list[str] = []


class OutlineSaveRequest(BaseModel):
    """人工保存的请求体：整份提纲，不做局部字段合并。"""

    title: str = ""
    angle: str = ""
    sections: list[OutlineSectionRequest] = []
