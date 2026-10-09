import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import { Button, ErrorState, PageHeader } from "../../components/ui";
import { formatTime, statusLabels } from "../../lib/display";
import { VersionCard } from "./VersionCard";

export function MessageDetailPage() {
  const { messageId = "" } = useParams();
  const [versionId, setVersionId] = useState("");
  const detail = useQuery({
    queryKey: ["message", messageId],
    refetchInterval: 3000,
    queryFn: async () => {
      const r = await api.GET("/api/messages/{message_id}", {
        params: { path: { message_id: messageId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const translate = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/messages/{message_id}/translate", {
        params: { path: { message_id: messageId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const enrich = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/messages/{message_id}/enrich", {
        params: { path: { message_id: messageId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const version =
    detail.data?.versions.find((v) => v.id === versionId) ??
    detail.data?.versions[0];
  /** 刚创建的任务优先，其次是该版本最近一次任务，用于显示真实排队状态。 */
  const pickup = <T extends { input_version_id: string | null; id: string }>(
    created: T | undefined,
    existing: { id: string; status: string } | null | undefined,
  ) => (created?.input_version_id === version?.id ? created : existing);
  const activeTask = pickup(translate.data, version?.translation_task);
  const activeEnrichmentTask = pickup(enrich.data, version?.enrichment_task);
  const task = useQuery({
    queryKey: ["task", activeTask?.id],
    enabled: Boolean(activeTask),
    refetchInterval: 2000,
    queryFn: async () => {
      const r = await api.GET("/api/tasks/{task_id}", {
        params: { path: { task_id: activeTask!.id } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const taskStatus = task.data?.status ?? activeTask?.status;
  const busy = (status: string | undefined) =>
    status === "queued" || status === "running";
  const isLatest = version?.id === detail.data?.versions[0]?.id;
  if (detail.isLoading) return <div className="loading">正在加载消息…</div>;
  if (detail.isError || !detail.data)
    return (
      <ErrorState
        message={detail.error?.message ?? "消息不存在"}
        onRetry={() => void detail.refetch()}
      />
    );
  return (
    <div className="page page-reading">
      <PageHeader
        title={version?.title ?? "消息详情"}
        description="原文与机器译文分别保存，历史版本可查看。"
        action={
          <Link
            className="button secondary"
            to={sessionStorage.getItem("messages-return") || "/messages"}
          >
            返回消息
          </Link>
        }
      />
      <div className="toolbar">
        <label htmlFor="version">内容版本</label>
        <select
          id="version"
          value={version?.id}
          onChange={(e) => setVersionId(e.target.value)}
        >
          {detail.data.versions.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.version_number} · {formatTime(v.collected_at)}
            </option>
          ))}
        </select>
        {version?.language === "en" && isLatest && (
          <Button
            disabled={translate.isPending || busy(taskStatus)}
            onClick={() => translate.mutate()}
          >
            {translate.isPending ? "创建任务…" : "生成中文"}
          </Button>
        )}
        {version && (
          <Button
            className="secondary"
            disabled={enrich.isPending || busy(activeEnrichmentTask?.status)}
            onClick={() => enrich.mutate()}
          >
            {enrich.isPending ? "创建任务…" : "生成摘要"}
          </Button>
        )}
        {activeTask && (
          <Link to="/tasks" className="muted" role="status">
            翻译任务：{statusLabels[taskStatus || "queued"]}，查看状态
          </Link>
        )}
        {activeEnrichmentTask && (
          <Link to="/tasks" className="muted" role="status">
            加工任务：{statusLabels[activeEnrichmentTask.status]}，查看状态
          </Link>
        )}
      </div>
      {translate.isError && <ErrorState message={translate.error.message} />}
      {enrich.isError && <ErrorState message={enrich.error.message} />}
      {task.isError && (
        <ErrorState
          message={task.error.message}
          onRetry={() => void task.refetch()}
        />
      )}
      {version && <VersionCard version={version} />}
    </div>
  );
}
