import { Fragment, useState } from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import type { components } from "../../api/generated";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { Pagination } from "../../components/Pagination";
import { sizeParams, usePaginationParams } from "../../lib/usePaginationParams";
import { formatDuration, formatTime, statusLabels } from "../../lib/display";
import { ClearFailedDialog, TaskDeleteDialog } from "./TaskDeleteDialog";

type Task = components["schemas"]["TaskResponse"];

const TASK_LABELS: Record<string, string> = {
  collect_source: "采集 RSS",
  enrich_message: "内容加工",
  translate_message: "翻译消息",
};

/** 任务已经结束的状态；只有这两种状态的耗时才有意义。 */
const FINISHED = new Set(["succeeded", "failed"]);

/** 目标链接：消息任务跳消息详情，采集任务跳来源详情。 */
function targetPath(task: Task): string | null {
  if (!task.target_id) return null;
  if (task.target_kind === "source") return `/sources/${task.target_id}`;
  if (task.target_kind === "message") return `/messages/${task.target_id}`;
  return null;
}

/** 耗时：排队等待也算在内，所以只在任务结束后显示。 */
function durationCell(task: Task) {
  if (!FINISHED.has(task.status)) return <span className="muted">—</span>;
  return formatDuration(task.updated_at - task.created_at);
}

export function TasksPage() {
  const { params, setParams, size, offset, setPageOffset, setSize } =
    usePaginationParams();
  const status = params.get("status") || "";
  const queryClient = useQueryClient();
  const tasks = useQuery({
    queryKey: ["tasks", status, size, offset],
    refetchInterval: 3000,
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/tasks", {
        params: { query: { status: status || undefined, limit: size, offset } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const [pendingDelete, setPendingDelete] = useState<Task | null>(null);
  const [clearing, setClearing] = useState(false);
  const rows = tasks.data?.items ?? [];
  const total = tasks.data?.total ?? 0;
  const retry = useMutation({
    mutationFn: async (id: string) => {
      const r = await api.POST("/api/tasks/{task_id}/retry", {
        params: { path: { task_id: id } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["tasks"] }),
  });
  return (
    <div className="page page-wide">
      <PageHeader
        title="任务"
        description="状态每 3 秒刷新；独立 worker 负责执行和恢复。目标是任务作用于的消息或来源。"
      />
      <Card>
        <div className="toolbar card-pad">
          <label htmlFor="task-status">状态</label>
          <select
            id="task-status"
            value={status}
            onChange={(e) =>
              setParams({ ...sizeParams(size), status: e.target.value })
            }
          >
            <option value="">全部</option>
            {["queued", "running", "succeeded", "failed"].map((value) => (
              <option key={value} value={value}>
                {statusLabels[value]}
              </option>
            ))}
          </select>
          <Button className="secondary" onClick={() => void tasks.refetch()}>
            刷新
          </Button>
          <Button className="danger-outline" onClick={() => setClearing(true)}>
            清理失败任务
          </Button>
        </div>
        {tasks.isLoading && <div className="loading">正在加载任务…</div>}
        {tasks.isError && (
          <ErrorState
            message={tasks.error.message}
            onRetry={() => void tasks.refetch()}
          />
        )}
        {retry.isError && <ErrorState message={retry.error.message} />}
        {total === 0 && (
          <EmptyState
            title={status ? "没有匹配的任务" : "还没有任务"}
            description="从来源详情触发采集，或从消息详情创建翻译任务。"
          />
        )}
        {total > 0 && rows.length === 0 && (
          // 有任务但这一页为空：通常是历史任务被清理后停在旧偏移上。
          <EmptyState
            title="这一页没有内容"
            description={`当前共 ${total} 个任务，这个偏移已经越过末尾。`}
            action={
              <Button className="secondary" onClick={() => setPageOffset(0)}>
                回到第一页
              </Button>
            }
          />
        )}
        {!!rows.length && (
          <div className="table-wrap">
            <table className="tasks-table">
              <colgroup>
                <col className="col-task" />
                <col className="col-target" />
                <col className="col-status" />
                <col className="col-attempts" />
                <col className="col-model" />
                <col className="col-duration" />
                <col className="col-created" />
                <col className="col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>目标</th>
                  <th>状态</th>
                  <th>尝试</th>
                  <th>模型 / 提示词</th>
                  <th title="从创建到结束的时间，含排队等待">耗时</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((task) => {
                  const path = targetPath(task);
                  return (
                    <Fragment key={task.id}>
                      <tr>
                        <td>
                          <strong>
                            {TASK_LABELS[task.task_type] ?? task.task_type}
                          </strong>
                          <div className="cell-subtitle" title={task.id}>
                            {task.id.slice(0, 8)}
                          </div>
                        </td>
                        <td>
                          {path ? (
                            <Link
                              className="cell-title"
                              title={task.target_label}
                              to={path}
                            >
                              {task.target_label}
                            </Link>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td className="cell-nowrap">
                          <Badge
                            tone={
                              task.status === "succeeded"
                                ? "success"
                                : task.status === "failed"
                                  ? "danger"
                                  : "warning"
                            }
                          >
                            {statusLabels[task.status]}
                          </Badge>
                        </td>
                        <td className="cell-nowrap">{task.attempts}</td>
                        <td>
                          {task.model ?? <span className="muted">—</span>}
                          {task.prompt_version && (
                            <div className="cell-subtitle">
                              {task.prompt_version}
                            </div>
                          )}
                        </td>
                        <td className="cell-nowrap">{durationCell(task)}</td>
                        <td className="cell-nowrap">
                          {formatTime(task.created_at)}
                        </td>
                        <td className="cell-nowrap">
                          {task.status === "failed" && (
                            <Button
                              className="secondary"
                              disabled={retry.isPending}
                              onClick={() => retry.mutate(task.id)}
                            >
                              重试任务
                            </Button>
                          )}
                          <Button
                            className="danger-outline"
                            disabled={!FINISHED.has(task.status)}
                            title={
                              FINISHED.has(task.status)
                                ? undefined
                                : "排队中或运行中的任务不能删除"
                            }
                            onClick={() => setPendingDelete(task)}
                          >
                            删除
                          </Button>
                        </td>
                      </tr>
                      {task.error_message && (
                        <tr className="task-error">
                          <td colSpan={8}>
                            <span className="field-error">
                              {task.error_message}
                            </span>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {total > 0 && (
          <Pagination
            total={total}
            offset={offset}
            size={size}
            unit="个任务"
            disabled={tasks.isPlaceholderData}
            onPage={setPageOffset}
            onSize={(next) => setSize(next, total)}
          />
        )}
      </Card>
      <TaskDeleteDialog
        task={pendingDelete}
        onClose={() => setPendingDelete(null)}
      />
      <ClearFailedDialog open={clearing} onClose={() => setClearing(false)} />
    </div>
  );
}
