import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
} from "../../components/ui";
import { formatTime, statusLabels } from "../../lib/display";
export function MessagesPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "";
  const source = params.get("source") || "";
  const offset = Number(params.get("offset") || 0);
  const messages = useQuery({
    queryKey: ["messages", q, source, offset],
    refetchInterval: 5000,
    queryFn: async () => {
      const r = await api.GET("/api/messages", {
        params: {
          query: {
            q: q || undefined,
            source_id: source || undefined,
            limit: 25,
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
  const names = new Map(sources.data?.map((s) => [s.id, s.name]));
  const filter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    next.delete("offset");
    setParams(next, { replace: true });
  };
  const page = (value: number) => {
    const next = new URLSearchParams(params);
    next.set("offset", String(value));
    setParams(next);
  };
  return (
    <div className="page">
      <PageHeader title="消息" description="原文、版本和机器翻译。" />
      <Card>
        <div className="toolbar card-pad">
          <Input
            aria-label="搜索消息"
            value={q}
            onChange={(e) => filter("q", e.target.value)}
            placeholder="搜索标题、摘要、正文"
          />
          <select
            aria-label="来源筛选"
            value={source}
            onChange={(e) => filter("source", e.target.value)}
          >
            <option value="">全部来源</option>
            {sources.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <Button className="secondary" onClick={() => setParams({})}>
            清空筛选
          </Button>
        </div>
        {messages.isLoading && <div className="loading">正在加载消息…</div>}
        {messages.isError && (
          <ErrorState
            message={messages.error.message}
            onRetry={() => void messages.refetch()}
          />
        )}
        {sources.isError && (
          <ErrorState
            message={sources.error.message}
            onRetry={() => void sources.refetch()}
          />
        )}
        {messages.data?.length === 0 && (
          <EmptyState
            title={q || source ? "没有匹配结果" : "还没有消息"}
            description="在来源详情触发采集，worker 执行后消息会出现在这里。"
          />
        )}
        {!!messages.data?.length && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>标题</th>
                  <th>来源</th>
                  <th>语言</th>
                  <th>版本</th>
                  <th>中文化</th>
                  <th>发布时间</th>
                </tr>
              </thead>
              <tbody>
                {messages.data.map((message) => {
                  const v = message.latest_version;
                  const t = v?.translations[0];
                  const translationStatus =
                    v?.translation_task?.status ?? t?.status;
                  return (
                    <tr key={message.id}>
                      <td>
                        <Link
                          className="cell-title"
                          onClick={() =>
                            sessionStorage.setItem(
                              "messages-return",
                              "/messages?" + params.toString(),
                            )
                          }
                          to={"/messages/" + message.id}
                        >
                          {v?.title || "无标题"}
                        </Link>
                        <div className="cell-subtitle">{v?.summary}</div>
                      </td>
                      <td>{names.get(message.source_id) || "未知"}</td>
                      <td>{statusLabels[v?.language || "auto"]}</td>
                      <td>v{v?.version_number}</td>
                      <td>
                        <Badge
                          tone={
                            translationStatus === "succeeded"
                              ? "success"
                              : translationStatus === "failed"
                                ? "danger"
                                : "neutral"
                          }
                        >
                          {translationStatus
                            ? statusLabels[translationStatus]
                            : "未翻译"}
                        </Badge>
                      </td>
                      <td>{formatTime(v?.published_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="toolbar card-pad">
          <Button
            className="secondary"
            disabled={offset === 0}
            onClick={() => page(Math.max(0, offset - 25))}
          >
            上一页
          </Button>
          <span className="muted">第 {offset / 25 + 1} 页</span>
          <Button
            className="secondary"
            disabled={(messages.data?.length ?? 0) < 25}
            onClick={() => page(offset + 25)}
          >
            下一页
          </Button>
        </div>
      </Card>
    </div>
  );
}
