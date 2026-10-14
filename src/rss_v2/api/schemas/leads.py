"""线索的请求和响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: 新建线索时的成员下限说明由服务层给出（中文错误信息），这里只挡空列表。
MIN_MESSAGE_IDS = 1


class LeadCreateRequest(BaseModel):
    """新建线索：标题必填，成员用消息 id（服务端取各自当前的最新版本）。"""

    title: str = Field(min_length=1, max_length=80, description="线索名，必填")
    note: str | None = Field(default=None, max_length=300, description="可选备注")
    message_ids: list[str] = Field(min_length=MIN_MESSAGE_IDS, description="成员消息 id")


class LeadMessagesRequest(BaseModel):
    """给线索追加成员：同样传消息 id。"""

    message_ids: list[str] = Field(min_length=MIN_MESSAGE_IDS)


class LeadMemberResponse(BaseModel):
    """线索成员：一条消息版本，以及界面需要的展示信息。"""

    message_id: str
    version_id: str
    version_number: int
    title: str
    source_id: str
    source_name: str
    published_at: int | None
    collected_at: int
    added_at: int


class LeadResponse(BaseModel):
    """线索本体与成员条数。"""

    id: str
    title: str
    note: str | None
    members: int
    created_at: int
    updated_at: int


class LeadDetailResponse(BaseModel):
    """线索详情：本体 + 成员列表（按发布时间从早到晚）。"""

    id: str
    title: str
    note: str | None
    created_at: int
    updated_at: int
    members: list[LeadMemberResponse]


class LeadListResponse(BaseModel):
    """线索列表。"""

    items: list[LeadResponse]
    total: int
    limit: int
    offset: int


class LeadMessagesResponse(BaseModel):
    """成员变动的结果：更新后的线索与本次新增条数。"""

    lead: LeadResponse
    added: int


class LeadMemberRemovalResponse(BaseModel):
    """移除成员的结果。"""

    lead: LeadResponse
    removed: int


class LeadDeletionResponse(BaseModel):
    """删除线索的结果。"""

    deleted: int
