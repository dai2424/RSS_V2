import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, PageHeader } from "../../components/ui";
import { formatTime } from "../../lib/display";
import { FactsPanel } from "./FactsPanel";
import { useProjectDetail } from "./hooks";
import { MaterialsPanel } from "./MaterialsPanel";

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
 * 写作项目工作台：分区展示资料、事实与核对。
 *
 * 提纲与全文属于后续任务（P4/P5）；在它们实现之前这里只展示已有的真实数据，
 * 不放置不可用的按钮。所有状态来自服务端，失败保留原因与重试入口。
 */
export function ProjectPage() {
  const { projectId = "" } = useParams();
  const [section, setSection] = useState<"materials" | "facts">("materials");
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
    <div className="page page-wide">
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
        <p className="muted">
          成员锁定在选题时的版本：消息后来更新不影响这里的材料与依据。
        </p>
      </Card>
      <div className="toolbar" role="tablist" aria-label="工作台分区">
        <button
          type="button"
          role="tab"
          aria-selected={section === "materials"}
          className={section === "materials" ? "button" : "button secondary"}
          onClick={() => setSection("materials")}
        >
          资料
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={section === "facts"}
          className={section === "facts" ? "button" : "button secondary"}
          onClick={() => setSection("facts")}
        >
          事实与核对
        </button>
      </div>
      {section === "materials" ? (
        <MaterialsPanel projectId={projectId} />
      ) : (
        <FactsPanel projectId={projectId} />
      )}
      <Card className="card-pad">
        <p className="muted">
          提纲确认与全文生成属于后续任务，当前不在这个页面提供入口；先把资料与事实核对清楚。
        </p>
      </Card>
    </div>
  );
}
