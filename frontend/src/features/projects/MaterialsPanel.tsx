import { useState } from "react";
import { Link } from "react-router-dom";
import { TaskStatusBanner } from "../../components/TaskStatus";
import { showToast } from "../../components/Toast";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
} from "../../components/ui";
import { formatTime } from "../../lib/display";
import { useMaterialDetail, useMaterials, usePrepareMaterials } from "./hooks";

/** 资料状态的中文说明：读到正文 / 只有摘要 / 来源不可用 / 读取失败。 */
const STATUS_LABELS: Record<string, string> = {
  read: "已读正文",
  summary_only: "仅摘要",
  unavailable: "来源不可用",
  failed: "读取失败",
};

const STATUS_TONES: Record<
  string,
  "success" | "warning" | "danger" | "neutral"
> = {
  read: "success",
  summary_only: "warning",
  unavailable: "danger",
  failed: "danger",
};

/** 资料面板：来源列表、原文入口、正文展开与缺口说明。 */
export function MaterialsPanel({ projectId }: { projectId: string }) {
  const materials = useMaterials(projectId);
  const prepare = usePrepareMaterials(projectId);
  const [expanded, setExpanded] = useState<string | null>(null);
  const detail = useMaterialDetail(projectId, expanded);

  if (materials.isLoading) return <div className="loading">正在加载资料…</div>;
  if (materials.isError) {
    return (
      <ErrorState
        message={materials.error.message}
        onRetry={() => void materials.refetch()}
      />
    );
  }
  const body = materials.data;
  const items = body?.items ?? [];
  const summary = body?.summary;
  const counts = body?.counts ?? {};
  const run = () =>
    prepare.mutate(false, {
      onSuccess: () =>
        showToast({ type: "info", content: "已创建资料准备任务。" }),
      onError: (error) => showToast({ type: "error", content: error.message }),
    });
  const reRun = () =>
    prepare.mutate(true, {
      onSuccess: () =>
        showToast({ type: "info", content: "已发起重新获取资料。" }),
      onError: (error) => showToast({ type: "error", content: error.message }),
    });

  return (
    <Card className="card-pad">
      <div className="toolbar">
        <h2>资料（{counts.total ?? items.length}）</h2>
        <div className="toolbar">
          <Button disabled={prepare.isPending} onClick={run}>
            {prepare.isPending
              ? "正在创建…"
              : items.length
                ? "补充资料"
                : "准备资料"}
          </Button>
          {items.length > 0 && (
            <Button
              className="secondary"
              disabled={prepare.isPending}
              onClick={reRun}
            >
              重新获取资料
            </Button>
          )}
        </div>
      </div>
      <TaskStatusBanner
        status={body?.task_status}
        queuedText="资料准备已排队，等待 worker 领取"
        runningText="正在读取资料（检索 + 抓取原文）…"
        doneText={
          items.length > 0
            ? `已完成：资料 ${counts.total ?? items.length} 条`
            : "资料准备已完成"
        }
        failedText="上次资料准备失败"
        retryLabel="重新获取资料"
        busy={prepare.isPending}
        onRetry={body?.task_status === "failed" ? reRun : undefined}
      />
      {prepare.isError && (
        <div className="field-error" role="alert">
          {prepare.error.message}
        </div>
      )}
      {items.length === 0 && !body?.task_status && (
        <EmptyState
          title="还没有资料"
          description="点「准备资料」读取这条选题的原文，并按事件关键词联网补充；读取失败的资料也会列出来。"
        />
      )}
      {summary && summary.queries.length > 0 && (
        <div className="digest-summary">
          <h3>本次检索</h3>
          <ul className="reason-list">
            {summary.queries.map((query) => (
              <li key={query.query}>
                <code>{query.query}</code>
                {query.purpose && (
                  <span className="muted"> · {query.purpose}</span>
                )}
                <span className="muted">
                  {" "}
                  · 命中 {query.hits} 条
                  {query.unresponsive_engines.length > 0 &&
                    ` · 未响应引擎：${query.unresponsive_engines.join("、")}`}
                  {query.error_code && ` · 检索失败（${query.error_code}）`}
                </span>
              </li>
            ))}
          </ul>
          <p className="muted">
            读到正文 {counts.read ?? 0} 条 · 仅摘要 {counts.summary_only ?? 0}{" "}
            条 · 读取失败 {counts.failed ?? 0} 条 · 重复跳过{" "}
            {summary.duplicates} 条 · 预算未读 {summary.skipped_by_budget} 条
          </p>
          {summary.notes.length > 0 && (
            <ul className="reason-list">
              {summary.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">资料</th>
                <th scope="col">状态</th>
                <th scope="col">时间</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <div className="cell-title">
                      {item.url ? (
                        <a href={item.url} target="_blank" rel="noreferrer">
                          {item.title || item.url}
                        </a>
                      ) : (
                        item.title || "（无标题）"
                      )}
                    </div>
                    <div className="cell-subtitle">
                      {item.kind === "rss" ? "订阅原文" : "联网补充"}
                      {item.publisher && ` · ${item.publisher}`}
                      {item.truncated && " · 已按体积上限截断"}
                      {item.error_code && ` · ${item.error_code}`}
                      {item.error_message && ` · ${item.error_message}`}
                    </div>
                  </td>
                  <td>
                    <Badge tone={STATUS_TONES[item.status] ?? "neutral"}>
                      {STATUS_LABELS[item.status] ?? item.status}
                    </Badge>
                  </td>
                  <td>
                    {formatTime(item.published_at ?? item.fetched_at)}
                    {item.message_version_id && (
                      <div className="cell-subtitle">
                        <Link to={`/messages/${item.message_version_id}`}>
                          按版本核对
                        </Link>
                      </div>
                    )}
                  </td>
                  <td>
                    <Button
                      className="secondary"
                      onClick={() =>
                        setExpanded(expanded === item.id ? null : item.id)
                      }
                    >
                      {expanded === item.id ? "收起正文" : "查看正文"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {expanded && (
        <div className="material-body">
          {detail.isLoading && <p className="muted">正在读取正文…</p>}
          {detail.isError && (
            <p className="field-error" role="alert">
              {detail.error.message}
            </p>
          )}
          {detail.data && (
            <>
              <div className="cell-subtitle">
                {detail.data.url} · {detail.data.byte_size} 字节 · 指纹{" "}
                {detail.data.content_hash.slice(0, 12)}
              </div>
              <pre className="material-text">
                {detail.data.content_text || "（没有正文）"}
              </pre>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
