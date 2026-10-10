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
import { usePaginationParams } from "../../lib/usePaginationParams";
import { formatDuration, formatTime, statusLabels } from "../../lib/display";
import { useDebouncedCallback } from "../../lib/useDebouncedCallback";
import { Input } from "../../components/ui";
import { RANGE_OPTIONS, rangeOf, sinceOf } from "../../lib/searchRange";
import { ClearTasksDialog, TaskDeleteDialog } from "./TaskDeleteDialog";

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
  const taskType = params.get("type") || "";
  const source = params.get("source") || "";
  const search = params.get("q") || "";
  const range = rangeOf(params.get("range"), false);
  const queryClient = useQueryClient();
  // 搜索框走防抖：每敲一个字就查一次任务表没有意义。
  const [draft, setDraft] = useState(search);
  const pushQuery = useDebouncedCallback((value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set("q", value);
    else next.delete("q");
    next.delete("offset");
    setParams(next, { replace: true });
  }, 300);
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next, { replace: true });
  };
  const tasks = useQuery({
    queryKey: ["tasks", status, taskType, source, search, range, size, offset],
    refetchInterval: 3000,
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/tasks", {
        params: {
          query: {
            status: status || undefined,
            task_type: taskType || undefined,
            source_id: source || undefined,
            q: search || undefined,
            since: sinceOf(range, Math.floor(Date.now() / 1000)),
            limit: size,
            offset,
          },
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const sources = useQuery({
    queryKey: ["source-options"],
    queryFn: async () => {
      const r = await api.GET("/api/sources", {
        params: { query: { limit: 100 } },
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
          <Input
            aria-label="搜索任务"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              pushQuery(e.target.value);
            }}
            placeholder="搜索消息标题或来源名"
          />
          <label htmlFor="task-type">类型</label>
          <select
            id="task-type"
            value={taskType}
            onChange={(e) => change("type", e.target.value)}
          >
            <option value="">全部类型</option>
            {Object.entries(TASK_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <label htmlFor="task-source">来源</label>
          <select
            id="task-source"
            value={source}
            onChange={(e) => change("source", e.target.value)}
          >
            <option value="">全部来源</option>
            {sources.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <label htmlFor="task-range">时间范围</label>
          <select
            id="task-range"
            value={range}
            onChange={(e) =>
              change("range", e.target.value === "all" ? "" : e.target.value)
            }
            title="按任务创建时间筛选"
          >
            {RANGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <label htmlFor="task-status">状态</label>
          <select
            id="task-status"
            value={status}
            onChange={(e) => change("status", e.target.value)}
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
            清理任务
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
      <ClearTasksDialog open={clearing} onClose={() => setClearing(false)} />
    </div>
  );
}
