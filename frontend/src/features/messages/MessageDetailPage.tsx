import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import { Button, ErrorState, PageHeader } from "../../components/ui";
import { formatTime, statusLabels } from "../../lib/display";
import { RelatedMessages } from "./RelatedMessages";
import { VersionCard } from "./VersionCard";

/** 排队或运行中：只有这两种状态要挡住重复入队，成功后按钮恢复可用。 */
function busy(status?: string): boolean {
  return status === "queued" || status === "running";
}

export function MessageDetailPage() {
  const { messageId = "" } = useParams();
  const queryClient = useQueryClient();
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
  // 任务状态只认服务端：本页每 3 秒刷新一次详情，新建任务后立刻失效重取。
  // 早先版本把 mutation 返回的任务快照当成状态来源，任务跑完之后界面会一直停在"已排队"。
  const created = () =>
    void queryClient.invalidateQueries({ queryKey: ["message", messageId] });
  const translate = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/messages/{message_id}/translate", {
        params: { path: { message_id: messageId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: created,
  });
  const enrich = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/messages/{message_id}/enrich", {
        params: { path: { message_id: messageId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: created,
  });
  const version =
    detail.data?.versions.find((v) => v.id === versionId) ??
    detail.data?.versions[0];
  // 翻译与加工都以"消息的最新版本"为单位（接口与幂等键如此，列表筛选也按最新版本算），
  // 因此历史版本只提供查看：按钮不再让用户以为加工的是眼前这一版。
  const isLatest = version?.id === detail.data?.versions[0]?.id;
  const translationTask = version?.translation_task;
  const enrichmentTask = version?.enrichment_task;
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
            disabled={translate.isPending || busy(translationTask?.status)}
            onClick={() => translate.mutate()}
          >
            {translate.isPending ? "创建任务…" : "生成中文"}
          </Button>
        )}
        {isLatest && (
          <Button
            className="secondary"
            disabled={enrich.isPending || busy(enrichmentTask?.status)}
            onClick={() => enrich.mutate()}
          >
            {enrich.isPending ? "创建任务…" : "生成摘要"}
          </Button>
        )}
        {!isLatest && (
          <span className="muted">
            历史版本只用于查看：翻译与内容加工在最新版本上进行。
          </span>
        )}
        {isLatest && translationTask && (
          <Link to="/tasks" className="muted" role="status">
            翻译任务：
            {statusLabels[translationTask.status] ?? translationTask.status}
            ，查看状态
          </Link>
        )}
        {isLatest && enrichmentTask && (
          <Link to="/tasks" className="muted" role="status">
            加工任务：
            {statusLabels[enrichmentTask.status] ?? enrichmentTask.status}
            ，查看状态
          </Link>
        )}
      </div>
      {translate.isError && <ErrorState message={translate.error.message} />}
      {enrich.isError && <ErrorState message={enrich.error.message} />}
      {version && <VersionCard version={version} actionable={isLatest} />}
      <RelatedMessages messageId={messageId} />
    </div>
  );
}
