import { Link, useParams } from "react-router-dom";
import { Badge, Card, PageHeader } from "../../components/ui";
import { formatTime } from "../../lib/display";
import { useProjectDetail } from "./hooks";

/** 写作项目状态的中文说明；后续阶段会在同一状态机上继续推进。 */
const STATUS_LABELS: Record<string, string> = {
  materials_pending: "待准备资料",
  materials_ready: "资料已读取",
  outline_ready: "提纲已有草稿",
  outline_confirmed: "提纲已确认",
  article_ready: "全文已生成",
  saved: "已有人工保存稿",
};

/**
 * 写作项目：已选定的事件与成员，以及后续步骤的说明。
 *
 * 资料准备、提纲与全文生成属于阶段 4 的后续任务（P3a 起）；在它们实现之前，
 * 这里只展示已有的真实数据，不放置不可用的按钮。
 */
export function ProjectPage() {
  const { projectId = "" } = useParams();
  const detail = useProjectDetail(projectId);

  if (detail.isLoading) return <div className="loading">正在加载写作项目…</div>;
  if (detail.isError || !detail.data) {
    return (
      <div className="page page-reading">
        <PageHeader
          title="写作项目"
          description={detail.error?.message ?? "项目不存在"}
        />
        <Link className="button secondary" to="/discover">
          返回选题
        </Link>
      </div>
    );
  }
  const { project, members } = detail.data;
  return (
    <div className="page page-reading">
      <PageHeader
        title={project.title}
        description={`状态：${STATUS_LABELS[project.status] ?? project.status} · 事件成员 ${members.length} 篇`}
        action={
          <Link className="button secondary" to="/discover">
            返回选题
          </Link>
        }
      />
      <Card className="card-pad">
        <div className="alert">
          资料准备、提纲确认与全文生成在后续任务中实现；当前只记录选题与事件成员，
          这些内容不会因为消息更新而改变（成员锁定在选题时的版本）。
        </div>
        <h2>事件成员（{members.length}）</h2>
        <ol className="related-list">
          {members.map((member) => (
            <li key={member.version_id} className="related-item">
              <div className="related-item-shared">
                <Badge>v{member.version_number}</Badge>
                <span className="muted">
                  {member.source_name} ·{" "}
                  {formatTime(member.published_at ?? member.collected_at)}
                </span>
              </div>
              <Link
                className="cell-title"
                to={`/messages/${member.message_id}`}
              >
                {member.title || "无标题"}
              </Link>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
