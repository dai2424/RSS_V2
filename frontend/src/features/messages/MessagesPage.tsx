import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useDebouncedCallback } from "../../lib/useDebouncedCallback";
import {
  RANGE_OPTIONS,
  rangeLabel,
  rangeOf,
  sinceOf,
} from "../../lib/searchRange";
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
import { Pagination } from "../../components/Pagination";
import { sizeParams, usePaginationParams } from "../../lib/usePaginationParams";
import { formatTime, preferredText, statusLabels } from "../../lib/display";
import { MessageDeleteDialog, type DeleteTarget } from "./MessageDeleteDialog";

type TaskStatus = "queued" | "running" | "succeeded" | "failed";

const SUMMARY_PLACEHOLDER = "（无摘要）";

/** 处理状态同时用文字和颜色表达，不依赖颜色区分。 */
function ProcessingBadges({
  translationStatus,
  enrichmentStatus,
}: {
  translationStatus?: string;
  enrichmentStatus?: string;
}) {
  const items = [
    ["译", translationStatus, "未翻译"],
    ["摘", enrichmentStatus, "未加工"],
  ] as const;
  return (
    <div className="badge-stack">
      {items.map(([label, status, empty]) => (
        <Badge
          key={label}
          tone={
            status === "succeeded"
              ? "success"
              : status === "failed"
                ? "danger"
                : "neutral"
          }
        >
          {label} {status ? statusLabels[status as TaskStatus] : empty}
        </Badge>
      ))}
    </div>
  );
}

export function MessagesPage() {
  const { params, setParams, size, offset, setPageOffset, setSize } =
    usePaginationParams();
  const q = params.get("q") || "";
  const source = params.get("source") || "";
  // 输入先落在本地状态，停顿 300ms 再写进 URL 触发请求：全文检索是全表扫文本。
  const [draft, setDraft] = useState(q);
  const pushQuery = useDebouncedCallback((value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set("q", value);
    else next.delete("q");
    next.delete("offset");
    setParams(next, { replace: true });
  }, 300);
  const range = rangeOf(params.get("range"), Boolean(q));
  const state = params.get("state") || "";
  const messages = useQuery({
    queryKey: ["messages", q, source, range, state, size, offset],
    // 带搜索词时把轮询放到 30 秒：反复全表扫文本只为刷新一份基本不变的搜索结果不值得。
    refetchInterval: q ? 30000 : 5000,
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/messages", {
        params: {
          query: {
            q: q || undefined,
            source_id: source || undefined,
            since: sinceOf(range, Math.floor(Date.now() / 1000)),
            state: state || undefined,
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
  const names = new Map(sources.data?.items.map((s) => [s.id, s.name]));
  const filter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    next.delete("offset");
    setParams(next, { replace: true });
    // 换筛选条件后旧的选择可能已经不在列表里，清掉以免误删看不见的行。
    setSelected(new Map());
  };
  // 选择与待删除目标都留在页面状态里；弹层只负责确认与执行。
  const [selected, setSelected] = useState<Map<string, DeleteTarget>>(
    new Map(),
  );
  const [pending, setPending] = useState<DeleteTarget[] | null>(null);
  const toggle = (target: DeleteTarget) =>
    setSelected((current) => {
      const next = new Map(current);
      if (next.has(target.id)) next.delete(target.id);
      else next.set(target.id, target);
      return next;
    });
  const rows = messages.data?.items ?? [];
  const total = messages.data?.total ?? 0;
  return (
    <div className="page page-wide">
      <PageHeader title="消息" description="原文、版本和机器翻译。" />
      <Card>
        <div className="toolbar card-pad">
          <Input
            aria-label="搜索消息"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              pushQuery(e.target.value);
            }}
            placeholder="搜索标题、摘要、正文、译文和关键词"
          />
          <select
            aria-label="时间范围"
            value={range}
            onChange={(e) => filter("range", e.target.value)}
            title="搜索默认只看最近一周；浏览默认显示全部时间"
          >
            {RANGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            aria-label="处理状态"
            value={state}
            onChange={(e) => filter("state", e.target.value)}
            title="按翻译与加工的结果筛选：未加工、加工失败、有任务在排队等"
          >
            <option value="">全部状态</option>
            <option value="untranslated">未翻译</option>
            <option value="translated">已翻译</option>
            <option value="unenriched">未加工</option>
            <option value="enriched">已加工</option>
            <option value="enrich_failed">加工失败</option>
            <option value="pending">有任务在排队</option>
          </select>
          <select
            aria-label="来源筛选"
            value={source}
            onChange={(e) => filter("source", e.target.value)}
          >
            <option value="">全部来源</option>
            {sources.data?.items.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <Button
            className="secondary"
            onClick={() => {
              setDraft("");
              // 清空筛选不重置每页条数：那是显示偏好，不是筛选条件。
              setParams(sizeParams(size));
            }}
          >
            清空筛选
          </Button>
        </div>
        {selected.size > 0 && (
          <div className="toolbar card-pad">
            <span className="muted">已选择 {selected.size} 条消息</span>
            <Button
              className="danger"
              onClick={() => setPending([...selected.values()])}
            >
              删除选中
            </Button>
            <Button
              className="secondary"
              onClick={() => setSelected(new Map())}
            >
              清空选择
            </Button>
          </div>
        )}
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
        {total === 0 && (
          <EmptyState
            title={q || source ? "没有匹配结果" : "还没有消息"}
            description={
              q && range !== "all"
                ? `当前只搜${rangeLabel(range)}的消息，更早的内容不在范围内。`
                : "在来源详情触发采集，worker 执行后消息会出现在这里。"
            }
            action={
              q && range !== "all" ? (
                <Button
                  className="secondary"
                  onClick={() => filter("range", "all")}
                >
                  扩大到全部时间
                </Button>
              ) : undefined
            }
          />
        )}
        {total > 0 && rows.length === 0 && (
          // 有内容但这一页为空：通常是删除后停在旧偏移上，或链接里的 offset 越界。
          <EmptyState
            title="这一页没有内容"
            description={`当前共 ${total} 条，这个偏移已经越过末尾。`}
            action={
              <Button className="secondary" onClick={() => setPageOffset(0)}>
                回到第一页
              </Button>
            }
          />
        )}
        {!!rows.length && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>选择</th>
                  <th>标题</th>
                  <th>来源</th>
                  <th>语言</th>
                  <th>版本</th>
                  <th>处理</th>
                  <th>发布时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((message) => {
                  const v = message.latest_version;
                  const enrichment = v?.enrichments.find(
                    (item) => item.status === "succeeded",
                  );
                  const translation = v?.translations.find(
                    (item) => item.status === "succeeded",
                  );
                  // 有中文机器内容时优先展示，机器生成必须标记出来。
                  const title = preferredText(
                    "title",
                    enrichment,
                    translation,
                    v?.title,
                  );
                  const summary = preferredText(
                    "summary",
                    enrichment,
                    translation,
                    v?.summary,
                  );
                  return (
                    <tr key={message.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`选择 ${title.text || "无标题"}`}
                          checked={selected.has(message.id)}
                          onChange={() =>
                            toggle({
                              id: message.id,
                              title: title.text || "无标题",
                            })
                          }
                        />
                      </td>
                      <td>
                        <Link
                          className="cell-title"
                          title={v?.title || undefined}
                          onClick={() =>
                            sessionStorage.setItem(
                              "messages-return",
                              "/messages?" + params.toString(),
                            )
                          }
                          to={"/messages/" + message.id}
                        >
                          {title.text || "无标题"}
                        </Link>
                        {title.machine && <Badge tone="neutral">机器</Badge>}
                        <div className="cell-subtitle clamp-2">
                          {summary.text || SUMMARY_PLACEHOLDER}
                          {summary.machine && "（机器生成）"}
                        </div>
                      </td>
                      <td>{names.get(message.source_id) || "未知"}</td>
                      <td>{statusLabels[v?.language || "auto"]}</td>
                      <td>v{v?.version_number}</td>
                      <td>
                        <ProcessingBadges
                          translationStatus={
                            v?.translation_task?.status ?? translation?.status
                          }
                          enrichmentStatus={
                            v?.enrichment_task?.status ?? enrichment?.status
                          }
                        />
                      </td>
                      <td>{formatTime(v?.published_at)}</td>
                      <td>
                        <div className="row-actions">
                          <Button
                            className="danger-outline"
                            onClick={() =>
                              setPending([
                                {
                                  id: message.id,
                                  title: title.text || "无标题",
                                },
                              ])
                            }
                          >
                            删除
                          </Button>
                        </div>
                      </td>
                    </tr>
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
            disabled={messages.isPlaceholderData}
            onPage={setPageOffset}
            onSize={(next) => setSize(next, total)}
          />
        )}
      </Card>
      <MessageDeleteDialog
        targets={pending}
        onClose={() => {
          setPending(null);
          setSelected(new Map());
        }}
      />
    </div>
  );
}
