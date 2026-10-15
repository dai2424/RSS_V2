"""选题发现与写作项目的领域对象 → 响应模型。

批次状态与任务状态分别呈现：批次是"上次执行结果"，任务是"现在是否在跑"；
界面用 task_status 显示排队/运行，用 batch.status 显示完成/失败原因。
"""

from __future__ import annotations

from rss_v2.api.schemas import (
    DiscoveryBatchDetailResponse,
    DiscoveryBatchResponse,
    DiscoveryCandidateResponse,
    DiscoveryMemberResponse,
    DiscoveryReasonResponse,
    ProjectDetailResponse,
    ProjectResponse,
)
from rss_v2.domain import LeadMember, WritingProject
from rss_v2.services.discovery import BatchView, CandidateDetail, DiscoveryDetail


def discovery_member_response(member: LeadMember) -> DiscoveryMemberResponse:
    return DiscoveryMemberResponse(
        message_id=member.message_id,
        version_id=member.version_id,
        version_number=member.version_number,
        title=member.title,
        source_id=member.source_id,
        source_name=member.source_name,
        published_at=member.published_at,
        collected_at=member.collected_at,
    )


def discovery_batch_response(view: BatchView, reused: bool = False) -> DiscoveryBatchResponse:
    batch = view.batch
    return DiscoveryBatchResponse(
        id=batch.id,
        status=batch.status,
        window_start=batch.window_start,
        window_end=batch.window_end,
        category_id=batch.category_id,
        category_name=view.category_name,
        source_id=batch.source_id,
        source_name=view.source_name,
        message_count=batch.message_count,
        candidate_count=batch.candidate_count,
        duplicate_members=batch.duplicate_members,
        out_of_range=batch.out_of_range,
        dropped_members=batch.dropped_members,
        dropped_candidates=batch.dropped_candidates,
        error_code=batch.error_code,
        error_message=batch.error_message,
        task_id=batch.task_id,
        task_status=view.task_status,
        reused=reused,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


def discovery_candidate_response(detail: CandidateDetail) -> DiscoveryCandidateResponse:
    candidate = detail.candidate
    return DiscoveryCandidateResponse(
        id=candidate.id,
        rank=candidate.rank,
        title=candidate.title,
        summary=candidate.summary,
        event_time=candidate.event_time,
        score=candidate.score,
        reasons=[
            DiscoveryReasonResponse(kind=reason.kind, text=reason.text)
            for reason in candidate.reasons
        ],
        member_count=candidate.member_count,
        dropped_members=candidate.dropped_members,
        lead_id=candidate.lead_id,
        project_id=detail.project_id,
        members=[discovery_member_response(member) for member in detail.members],
    )


def discovery_detail_response(
    detail: DiscoveryDetail, view: BatchView, reused: bool = False
) -> DiscoveryBatchDetailResponse:
    return DiscoveryBatchDetailResponse(
        batch=discovery_batch_response(view, reused),
        candidates=[discovery_candidate_response(item) for item in detail.candidates],
    )


def project_response(project: WritingProject, reused: bool = False) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        title=project.title,
        status=project.status,
        lead_id=project.lead_id,
        candidate_id=project.candidate_id,
        reused=reused,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_detail_response(
    project: WritingProject,
    members: tuple[LeadMember, ...],
    counts: dict[str, int],
    reused: bool = False,
) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        project=project_response(project, reused),
        members=[discovery_member_response(member) for member in members],
        counts=counts,
    )
