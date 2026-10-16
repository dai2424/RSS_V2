import type { ReactNode } from "react";
import { Button } from "./ui";

/**
 * 后台任务的显眼状态条：排队、运行中、失败、完成各一种样式。
 *
 * 任务状态来自服务端（`task_status`），这里只负责"看得见"：运行中带转圈与说明，
 * 失败给出原因入口和重试按钮，完成给明确确认——不用一行灰色小字提示。
 */
export function TaskStatusBanner({
  status,
  queuedText = "任务已排队，等待 worker 领取",
  runningText = "正在执行…",
  doneText = "已完成",
  failedText = "上次执行失败",
  onRetry,
  retryLabel = "重试",
  busy = false,
  extra,
}: {
  status: string | null | undefined;
  queuedText?: string;
  runningText?: string;
  doneText?: string;
  failedText?: string;
  onRetry?: () => void;
  retryLabel?: string;
  busy?: boolean;
  extra?: ReactNode;
}) {
  if (!status) return null;
  if (status === "queued" || status === "running") {
    const running = status === "running";
    return (
      <div
        className={running ? "task-status task-status-running" : "task-status"}
        role="status"
      >
        <span className="task-spinner" aria-hidden="true" />
        <div>
          <strong>{running ? runningText : queuedText}</strong>
          <span className="muted">
            {" "}
            {running
              ? "可以离开这个页面，完成后回来刷新即可；进度以服务端为准。"
              : "worker 是独立进程，领取后会开始执行。"}
          </span>
        </div>
      </div>
    );
  }
  if (status === "failed") {
    return (
      <div className="task-status task-status-failed" role="status">
        <span aria-hidden="true">!</span>
        <div>
          <strong>{failedText}</strong>
          <span className="muted">
            {" "}
            失败原因与尝试次数见<a href="/tasks">任务页</a>
            ；重试会从已有产物继续，不会重复读取已获得的资料。
          </span>
          {extra}
        </div>
        {onRetry && (
          <Button className="secondary" disabled={busy} onClick={onRetry}>
            {busy ? "正在创建…" : retryLabel}
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="task-status task-status-done" role="status">
      <span aria-hidden="true">✓</span>
      <div>
        <strong>{doneText}</strong>
        {extra}
      </div>
    </div>
  );
}
