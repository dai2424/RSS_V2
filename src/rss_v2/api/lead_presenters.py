"""线索响应转换：领域对象 → API 响应模型。"""

from __future__ import annotations

from rss_v2.api.schemas import (
    LeadDetailResponse,
    LeadMemberResponse,
    LeadResponse,
)
from rss_v2.domain import Lead, LeadDetail, LeadMember, LeadSummary


def lead_response(lead: Lead, members: int) -> LeadResponse:
    """线索一行：本体 + 成员条数。"""

    return LeadResponse(
        id=lead.id,
        title=lead.title,
        note=lead.note,
        members=members,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


def lead_summary_response(summary: LeadSummary) -> LeadResponse:
    """列表行。"""

    return lead_response(summary.lead, summary.members)


def lead_member_response(member: LeadMember) -> LeadMemberResponse:
    """成员一行；version_id 是草稿引用与移除成员时的键。"""

    return LeadMemberResponse(
        message_id=member.message_id,
        version_id=member.version_id,
        version_number=member.version_number,
        title=member.title,
        source_id=member.source_id,
        source_name=member.source_name,
        published_at=member.published_at,
        collected_at=member.collected_at,
        added_at=member.added_at,
    )


def lead_detail_response(detail: LeadDetail) -> LeadDetailResponse:
    """详情：本体 + 成员列表。"""

    return LeadDetailResponse(
        id=detail.lead.id,
        title=detail.lead.title,
        note=detail.lead.note,
        created_at=detail.lead.created_at,
        updated_at=detail.lead.updated_at,
        members=[lead_member_response(member) for member in detail.members],
    )
