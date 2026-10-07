import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { components } from "../../api/generated";
import { api, requireResponse } from "../../api/client";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "../../components/ui/tabs";
import { formatTime, safeLink, statusLabels } from "../../lib/display";

type Version = components["schemas"]["MessageVersionResponse"];
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
  const version =
    detail.data?.versions.find((v) => v.id === versionId) ??
    detail.data?.versions[0];
  const activeTask =
    translate.data?.input_version_id === version?.id
      ? translate.data
      : version?.translation_task;
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
  if (detail.isLoading) return <div className="loading">正在加载消息…</div>;
  if (detail.isError || !detail.data)
    return (
      <ErrorState
        message={detail.error?.message ?? "消息不存在"}
        onRetry={() => void detail.refetch()}
      />
    );
  return (
    <div className="page">
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
        {version?.language === "en" &&
          version.id === detail.data.versions[0]?.id && (
            <Button
              disabled={
                translate.isPending ||
                taskStatus === "queued" ||
                taskStatus === "running"
              }
              onClick={() => translate.mutate()}
            >
              {translate.isPending ? "创建任务…" : "生成中文"}
            </Button>
          )}
        {activeTask && (
          <Link to="/tasks" className="muted" role="status">
            翻译任务：{statusLabels[taskStatus || "queued"]}，查看状态
          </Link>
        )}
      </div>
      {translate.isError && <ErrorState message={translate.error.message} />}
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
function VersionCard({ version }: { version: Version }) {
  const translation =
    version.translations.find((v) => v.status === "succeeded") ??
    version.translations[0];
  const href = safeLink(version.url);
  return (
    <Card className="version-card">
      <div className="version-meta">
        <Badge>v{version.version_number}</Badge>
        <Badge>{statusLabels[version.language]}</Badge>
        <span className="muted">
          发布：{formatTime(version.published_at)} · 采集：
          {formatTime(version.collected_at)}
        </span>
        {href && (
          <a className="muted" href={href} target="_blank" rel="noreferrer">
            原文链接 ↗
          </a>
        )}
      </div>
      <Tabs defaultValue="original">
        <TabsList>
          <TabsTrigger value="original">原文</TabsTrigger>
          <TabsTrigger value="chinese">中文版本（机器生成）</TabsTrigger>
        </TabsList>
        <TabsContent value="original">
          <article className="prose">
            <h2>{version.title}</h2>
            <h3>摘要</h3>
            <p>{version.summary || "无摘要"}</p>
            <h3>正文</h3>
            <p>{version.content || "无正文"}</p>
          </article>
        </TabsContent>
        <TabsContent value="chinese">
          {translation?.status === "succeeded" ? (
            <article className="prose">
              <h2>{translation.title}</h2>
              <h3>摘要</h3>
              <p>{translation.summary}</p>
              <h3>正文</h3>
              <p>{translation.content}</p>
              <p className="muted">
                模型：{translation.model} · 机器翻译，请核对原文。
              </p>
              <details className="muted">
                <summary>翻译记录</summary>
                <dl className="metadata-list">
                  <dt>提示词版本</dt>
                  <dd>{translation.prompt_version}</dd>
                  <dt>Provider</dt>
                  <dd>{translation.provider_id}</dd>
                  <dt>任务 ID</dt>
                  <dd>{translation.task_id}</dd>
                </dl>
              </details>
            </article>
          ) : (
            <div className="alert">
              {translation
                ? "翻译状态：" +
                  statusLabels[translation.status] +
                  " " +
                  (translation.error_message || "")
                : "此版本尚无译文。任务排队状态请在任务页查看。"}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </Card>
  );
}
