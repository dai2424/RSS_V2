import { useState } from "react";
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
import {
  useDigest,
  useDigestMaterials,
  useDigestRuns,
  useResolveNote,
} from "./hooks";

/** 事实类型与状态的中文说明。 */
const KIND_LABELS: Record<string, string> = {
  fact: "事实",
  analysis: "分析",
  speculation: "推测",
};

const STATUS_LABELS: Record<string, string> = {
  supported: "有支持证据",
  conflicting: "来源冲突",
  missing: "缺料待核对",
};

const STATUS_TONES: Record<string, "success" | "warning" | "danger"> = {
  supported: "success",
  conflicting: "warning",
  missing: "danger",
};

const NOTE_LABELS: Record<string, string> = {
  gap: "缺口",
  conflict: "分歧",
  note: "提示",
};

/** 核对面板：事实与证据、分歧与缺口、历史资料包版本。 */
export function FactsPanel({ projectId }: { projectId: string }) {
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const digest = useDigest(projectId, runId);
  const runs = useDigestRuns(projectId);
  const request = useDigestMaterials(projectId);
  const resolve = useResolveNote(projectId);

  if (digest.isLoading) return <div className="loading">正在加载事实清单…</div>;
  if (digest.isError) {
    const notFound = digest.error.message.includes("还没有资料整理");
    return (
      <Card className="card-pad">
        <div className="toolbar">
          <h2>事实与核对</h2>
          <Button
            disabled={request.isPending}
            onClick={() =>
              request.mutate(false, {
                onSuccess: () =>
                  showToast({ type: "info", content: "已创建资料整理任务。" }),
                onError: (error) =>
                  showToast({ type: "error", content: error.message }),
              })
            }
          >
            {request.isPending ? "正在创建…" : "整理资料"}
          </Button>
        </div>
        {notFound ? (
          <EmptyState
            title="还没有整理过"
            description="点「整理资料」把已读资料整理成关键事实与证据，并列出分歧与缺口。"
          />
        ) : (
          <ErrorState
            message={digest.error.message}
            onRetry={() => void digest.refetch()}
          />
        )}
        {request.isError && (
          <div className="field-error" role="alert">
            {request.error.message}
          </div>
        )}
      </Card>
    );
  }
  const body = digest.data;
  const facts = body?.facts ?? [];
  const notes = body?.notes ?? [];
  const counts = body?.run.counts ?? {};
  const openNotes = notes.filter((note) => note.resolved_at === null);
  const blocking = openNotes.filter((note) => note.blocking);
  const retry = () =>
    request.mutate(true, {
      onSuccess: () => showToast({ type: "info", content: "已发起重新整理。" }),
      onError: (error) => showToast({ type: "error", content: error.message }),
    });

  return (
    <>
      <Card className="card-pad">
        <div className="toolbar">
          <h2>事实与核对</h2>
          <div className="toolbar">
            <Button
              disabled={request.isPending}
              onClick={() =>
                request.mutate(false, {
                  onSuccess: () =>
                    showToast({
                      type: "info",
                      content: "已创建资料整理任务。",
                    }),
                  onError: (error) =>
                    showToast({ type: "error", content: error.message }),
                })
              }
            >
              整理资料
            </Button>
            <Button
              className="secondary"
              disabled={request.isPending}
              onClick={() =>
                request.mutate(true, {
                  onSuccess: () =>
                    showToast({ type: "info", content: "已发起重新整理。" }),
                  onError: (error) =>
                    showToast({ type: "error", content: error.message }),
                })
              }
            >
              重新整理
            </Button>
          </div>
        </div>
        <TaskStatusBanner
          status={body?.task_status}
          queuedText="资料整理已排队，等待 worker 领取"
          runningText="正在整理资料（分批调用模型）…"
          doneText={
            facts.length > 0
              ? `整理完成：事实 ${facts.length} 条，引文 ${counts.evidence ?? 0} 条`
              : "整理完成"
          }
          failedText="上次整理失败"
          retryLabel="重新整理"
          busy={request.isPending}
          onRetry={body?.task_status === "failed" ? retry : undefined}
        />
        {request.isError && (
          <div className="field-error" role="alert">
            {request.error.message}
          </div>
        )}
        <p className="muted">
          事实 {counts.fact ?? 0} · 分析 {counts.analysis ?? 0} · 推测{" "}
          {counts.speculation ?? 0} · 引文 {counts.evidence ?? 0} 条
          {(counts.rejected_quotes ?? 0) > 0 &&
            ` · 引文在资料里找不到 ${counts.rejected_quotes} 条（已丢弃）`}
          {(counts.out_of_range ?? 0) > 0 &&
            ` · 越界引用 ${counts.out_of_range} 条（已丢弃）`}
        </p>
        {body?.has_blocking && (
          <div className="alert" role="alert">
            还有 {blocking.length}{" "}
            条阻塞缺口未处理：不解决之前，不建议按现有资料直接写主线。
          </div>
        )}
        {facts.length === 0 ? (
          <EmptyState
            title={
              body?.task_status ? "最近一次整理还没有结果" : "还没有整理出事实"
            }
            description={
              body?.task_status
                ? "先看上面的状态提示：排队或运行中请稍等，失败则按提示重试；也可以在任务页查看错误分类。"
                : "资料可能太少或模型没有给出可核对的引文；补资料后重新整理。"
            }
          />
        ) : (
          <ol className="related-list">
            {facts.map((fact) => (
              <li key={fact.id} className="related-item">
                <div className="related-item-shared">
                  <Badge tone={fact.kind === "fact" ? "neutral" : "warning"}>
                    {KIND_LABELS[fact.kind] ?? fact.kind}
                  </Badge>
                  <Badge tone={STATUS_TONES[fact.status] ?? "neutral"}>
                    {STATUS_LABELS[fact.status] ?? fact.status}
                  </Badge>
                  <span className="muted">
                    {fact.evidence.length} 条引文
                    {fact.conflict_note && ` · ${fact.conflict_note}`}
                  </span>
                </div>
                <strong>{fact.statement}</strong>
                {fact.evidence.length > 0 && (
                  <ul className="reason-list">
                    {fact.evidence.map((item) => (
                      <li key={item.id}>
                        <blockquote>「{item.quote}」</blockquote>
                        <span className="muted">
                          来自资料 {item.material_id.slice(0, 8)}
                          {item.locator >= 0 &&
                            ` · 正文第 ${item.locator} 字起`}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </Card>
      <Card className="card-pad">
        <h2>分歧与缺口（{openNotes.length}）</h2>
        {openNotes.length === 0 ? (
          <p className="muted">没有待处理的缺口或分歧。</p>
        ) : (
          <ul className="related-list">
            {openNotes.map((note) => (
              <li key={note.id} className="related-item">
                <div className="related-item-shared">
                  <Badge tone={note.kind === "gap" ? "warning" : "neutral"}>
                    {NOTE_LABELS[note.kind] ?? note.kind}
                  </Badge>
                  {note.blocking && <Badge tone="danger">影响主线</Badge>}
                </div>
                <span>{note.text}</span>
                <div className="toolbar">
                  <Button
                    className="secondary"
                    disabled={resolve.isPending}
                    onClick={() =>
                      resolve.mutate(note.id, {
                        onSuccess: () =>
                          showToast({
                            type: "success",
                            content: "已标记为处理完成。",
                          }),
                        onError: (error) =>
                          showToast({ type: "error", content: error.message }),
                      })
                    }
                  >
                    标记已处理
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {notes.some((note) => note.resolved_at !== null) && (
          <details>
            <summary>
              已处理（{notes.filter((note) => note.resolved_at !== null).length}
              ）
            </summary>
            <ul className="reason-list">
              {notes
                .filter((note) => note.resolved_at !== null)
                .map((note) => (
                  <li key={note.id}>
                    {note.text}
                    <span className="muted">
                      {" "}
                      · {formatTime(note.resolved_at)}
                    </span>
                  </li>
                ))}
            </ul>
          </details>
        )}
      </Card>
      <Card className="card-pad">
        <h2>资料包版本</h2>
        <ul className="reason-list">
          {(runs.data?.items ?? []).map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="link-button"
                onClick={() =>
                  setRunId(item.id === body?.run.id ? undefined : item.id)
                }
              >
                {formatTime(item.created_at)} · 事实 {item.counts.fact ?? 0} ·
                引文 {item.counts.evidence ?? 0}
                {item.id === body?.run.id ? "（当前）" : ""}
              </button>
              <span className="muted">
                {" "}
                · 输入指纹 {item.materials_hash.slice(0, 8) || "（失败）"}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
