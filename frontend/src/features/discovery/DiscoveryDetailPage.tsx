import { Link, useNavigate, useParams } from "react-router-dom";
import { showToast } from "../../components/Toast";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { formatTime } from "../../lib/display";
import { useDiscoveryDetail, useSelectCandidate } from "./hooks";

/** 单条候选：概要、时间、成员报道、推荐理由与「写这篇」入口。 */
function Candidate({
  candidate,
  onSelect,
  selecting,
}: {
  candidate: {
    id: string;
    rank: number;
    title: string;
    summary: string;
    event_time: number | null;
    score: number;
    member_count: number;
    dropped_members: number;
    project_id: string | null;
    reasons: { kind: string; text: string }[];
    members: {
      version_id: string;
      message_id: string;
      version_number: number;
      title: string;
      source_name: string;
      published_at: number | null;
      collected_at: number;
    }[];
  };
  onSelect: () => void;
  selecting: boolean;
}) {
  return (
    <li className="related-item">
      <div className="related-item-shared">
        <Badge>推荐度 {candidate.score}</Badge>
        <span className="muted">
          {candidate.event_time ? formatTime(candidate.event_time) : "时间未知"}{" "}
          · {candidate.member_count} 篇报道
          {candidate.dropped_members > 0 &&
            `（另有 ${candidate.dropped_members} 篇未纳入）`}
        </span>
      </div>
      <strong>{candidate.title}</strong>
      <p className="muted">{candidate.summary}</p>
      <ul className="reason-list">
        {candidate.reasons.map((reason) => (
          <li key={`${reason.kind}-${reason.text}`}>
            <Badge tone={reason.kind === "important" ? "warning" : "neutral"}>
              {reason.kind === "important" ? "重要" : "有趣"}
            </Badge>{" "}
            {reason.text}
          </li>
        ))}
      </ul>
      <h4>相关报道（{candidate.members.length}）</h4>
      <ol className="related-list">
        {candidate.members.map((member) => (
          <li key={member.version_id} className="related-item">
            <div className="related-item-shared">
              <Badge>v{member.version_number}</Badge>
              <span className="muted">
                {member.source_name} ·{" "}
                {formatTime(member.published_at ?? member.collected_at)}
              </span>
            </div>
            <Link className="cell-title" to={`/messages/${member.message_id}`}>
              {member.title || "无标题"}
            </Link>
          </li>
        ))}
      </ol>
      <div className="toolbar">
        {candidate.project_id ? (
          <Link
            className="button secondary"
            to={`/projects/${candidate.project_id}`}
          >
            打开写作项目
          </Link>
        ) : (
          <Button disabled={selecting} onClick={onSelect}>
            {selecting ? "正在建立…" : "写这篇"}
          </Button>
        )}
      </div>
    </li>
  );
}

/**
 * 批次详情：候选事件的概要、成员报道、推荐理由与选择入口。
 *
 * 状态与候选都来自服务端；批次失败时显示服务端给出的原因，不伪装成"没有选题"。
 */
export function DiscoveryDetailPage() {
  const { batchId = "" } = useParams();
  const navigate = useNavigate();
  const detail = useDiscoveryDetail(batchId);
  const select = useSelectCandidate((projectId) => {
    showToast({ type: "success", content: "已选定事件，进入写作项目。" });
    navigate(`/projects/${projectId}`);
  });

  if (detail.isLoading) return <div className="loading">正在加载候选…</div>;
  if (detail.isError || !detail.data) {
    return (
      <ErrorState
        message={detail.error?.message ?? "这次发现不存在"}
        onRetry={() => void detail.refetch()}
      />
    );
  }
  const { batch, candidates } = detail.data;
  const status = batch.task_status === "queued" ? "queued" : batch.status;
  const statusLabel =
    {
      queued: "已排队",
      running: "运行中",
      succeeded: "已完成",
      failed: "失败",
    }[status] ?? status;
  return (
    <div className="page page-wide">
      <PageHeader
        title={batch.source_name ?? batch.category_name ?? "全部订阅"}
        description={`生效范围：${formatTime(batch.window_start)} 至 ${formatTime(batch.window_end)} · 消息 ${batch.message_count} 条 · 候选 ${batch.candidate_count} 个`}
        action={
          <Link className="button secondary" to="/discover">
            返回选题
          </Link>
        }
      />
      <Card className="card-pad">
        <div className="toolbar">
          <Badge
            tone={
              status === "failed"
                ? "danger"
                : status === "succeeded"
                  ? "success"
                  : "warning"
            }
          >
            {statusLabel}
          </Badge>
          {(batch.out_of_range > 0 ||
            batch.duplicate_members > 0 ||
            batch.dropped_candidates > 0 ||
            batch.dropped_members > 0) && (
            <span className="muted">
              {batch.out_of_range > 0 &&
                `越界或失效成员 ${batch.out_of_range} 条 · `}
              {batch.duplicate_members > 0 &&
                `重复成员 ${batch.duplicate_members} 条 · `}
              {batch.dropped_members > 0 &&
                `超上限未纳入 ${batch.dropped_members} 条 · `}
              {batch.dropped_candidates > 0 &&
                `未展示候选 ${batch.dropped_candidates} 个 · `}
              计数来自服务端，不是估算。
            </span>
          )}
        </div>
        {status === "failed" && (
          <div className="alert" role="alert">
            {batch.error_message ??
              "这次发现失败，请查看任务列表里的错误分类后重试。"}{" "}
            失败原因与任务分类见
            <Link to="/tasks">任务列表</Link>
            ；重新点击「发现选题」会复用同一批次重试。
          </div>
        )}
      </Card>
      {candidates.length === 0 && status === "succeeded" && (
        <EmptyState
          title="这次没有值得写的选题"
          description="模型没有从这个窗口里选出候选。可以扩大时间范围、换分类或来源后再试。"
          action={
            <Link className="button secondary" to="/discover">
              调整范围
            </Link>
          }
        />
      )}
      {candidates.length > 0 && (
        <Card className="card-pad">
          <h2>候选事件（{candidates.length}）</h2>
          <ol className="related-list">
            {candidates.map((candidate) => (
              <Candidate
                key={candidate.id}
                candidate={candidate}
                selecting={select.isPending}
                onSelect={() =>
                  select.mutate(
                    { batchId, candidateId: candidate.id },
                    {
                      onError: (error) =>
                        showToast({ type: "error", content: error.message }),
                    },
                  )
                }
              />
            ))}
          </ol>
        </Card>
      )}
      {select.isError && (
        <div className="field-error" role="alert">
          {select.error.message}
        </div>
      )}
    </div>
  );
}
