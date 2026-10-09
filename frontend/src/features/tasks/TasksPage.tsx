import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { formatTime, statusLabels } from "../../lib/display";

export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") || "";
  const offset = Number(params.get("offset") || 0);
  const queryClient = useQueryClient();
  const tasks = useQuery({
    queryKey: ["tasks", status, offset],
    refetchInterval: 3000,
    queryFn: async () => {
      const r = await api.GET("/api/tasks", {
        params: { query: { status: status || undefined, limit: 25, offset } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
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
    <div className="page">
      <PageHeader
        title="任务"
        description="状态每 3 秒刷新；独立 worker 负责执行和恢复。"
      />
      <Card>
        <div className="toolbar card-pad">
          <label htmlFor="task-status">状态</label>
          <select
            id="task-status"
            value={status}
            onChange={(e) => setParams({ status: e.target.value })}
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
        </div>
        {tasks.isLoading && <div className="loading">正在加载任务…</div>}
        {tasks.isError && (
          <ErrorState
            message={tasks.error.message}
            onRetry={() => void tasks.refetch()}
          />
        )}
        {retry.isError && <ErrorState message={retry.error.message} />}
        {tasks.data?.length === 0 && (
          <EmptyState
            title="没有匹配的任务"
            description="从来源详情触发采集，或从消息详情创建翻译任务。"
          />
        )}
        {!!tasks.data?.length && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>状态</th>
                  <th>尝试</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {tasks.data.map((task) => (
                  <tr key={task.id}>
                    <td>
                      <strong>
                        {task.task_type === "collect_source"
                          ? "采集 RSS"
                          : task.task_type === "enrich_message"
                            ? "内容加工"
                            : "翻译消息"}
                      </strong>
                      <div className="cell-subtitle">{task.id}</div>
                      {task.error_message && (
                        <span className="field-error">
                          {task.error_message}
                        </span>
                      )}
                    </td>
                    <td>
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
                    <td>{task.attempts}</td>
                    <td>{formatTime(task.created_at)}</td>
                    <td>
                      {task.status === "failed" && (
                        <Button
                          className="secondary"
                          disabled={retry.isPending}
                          onClick={() => retry.mutate(task.id)}
                        >
                          重试任务
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="toolbar card-pad">
          <Button
            className="secondary"
            disabled={offset === 0}
            onClick={() =>
              setParams({ status, offset: String(Math.max(0, offset - 25)) })
            }
          >
            上一页
          </Button>
          <span className="muted">第 {offset / 25 + 1} 页</span>
          <Button
            className="secondary"
            disabled={(tasks.data?.length ?? 0) < 25}
            onClick={() => setParams({ status, offset: String(offset + 25) })}
          >
            下一页
          </Button>
        </div>
      </Card>
    </div>
  );
}
