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
import type { components } from "../../api/generated";
import { ArticleView } from "./ArticleView";
import { useArticles, useArticlePreview, useGenerateArticle } from "./hooks";

type Counters = components["schemas"]["ArticleCountersResponse"];
type Summary = components["schemas"]["ArticleSummaryResponse"];

/** 版本来源与状态：AI 生成稿与人工保存稿必须分开显示，不共用一个徽标。 */
const KIND_LABELS: Record<string, string> = {
  generated: "AI 生成稿",
  edited: "人工保存稿",
};
const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  saved: "已保存",
};

/** 丢弃/降级计数翻成中文：每一项都要能读懂"丢了什么"，不静默。 */
function counterNotes(counters: Counters): string[] {
  const notes: string[] = [];
  const add = (value: number, text: string) => {
    if (value > 0) notes.push(`${text} ${value} 条`);
  };
  add(counters.out_of_range, "越界引用（已丢弃）");
  add(counters.unsupported, "引用缺引文事实（已丢弃）");
  add(counters.unused_citations, "未被正文使用的引用（已丢弃）");
  add(counters.duplicate_markers, "重复标记（已丢弃）");
  add(counters.invalid_markers, "非法标记（已丢弃）");
  add(counters.downgraded_speculation, "推测缺不确定表述（已降级为分析）");
  add(counters.unknown_kind, "未知段落类型（已按分析处理）");
  add(counters.rejected_links, "非法链接（已还原为文字）");
  add(counters.dropped_paragraphs, "空段落（已丢弃）");
  add(counters.dropped_sections, "超限或空小节（已丢弃）");
  add(counters.invalid_sources, "自造来源（已丢弃）");
  add(counters.unused_sources, "未被引用的来源（已丢弃）");
  add(counters.added_sources, "模型漏列、服务端补上的来源");
  add(counters.dropped_sources, "超出上限的来源（已丢弃）");
  return notes;
}

/**
 * 文章分区：按已确认提纲生成全文、查看版本、阅读与就地核对引用。
 *
 * 阻塞缺口只做显著标记、不拦生成（与提纲确认同一口径）；预览里的正文由服务端渲染，
 * 点引用标记就地展开证据，不离开阅读位置。
 */
export function ArticlesPanel({ projectId }: { projectId: string }) {
  const articles = useArticles(projectId);
  const generate = useGenerateArticle(projectId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const versions = articles.data?.items ?? [];
  const selected: Summary | undefined =
    versions.find((item) => item.id === selectedId) ?? versions[0];
  const preview = useArticlePreview(projectId, selected?.id ?? null);

  if (articles.isLoading) return <div className="loading">正在加载全文…</div>;
  if (articles.isError || !articles.data) {
    return (
      <Card className="card-pad">
        <ErrorState
          message={articles.error?.message ?? "全文加载失败"}
          onRetry={() => void articles.refetch()}
        />
      </Card>
    );
  }

  const body = articles.data;
  const request = (force: boolean) =>
    generate.mutate(force, {
      onSuccess: () =>
        showToast({
          type: "info",
          content: force ? "已发起重新生成。" : "已创建全文生成任务。",
        }),
      onError: (error) => showToast({ type: "error", content: error.message }),
    });

  return (
    <>
      <Card className="card-pad">
        <div className="toolbar">
          <h2>文章</h2>
          <div className="toolbar">
            <Button
              disabled={generate.isPending || !body.outline_version_id}
              onClick={() => request(false)}
            >
              生成全文
            </Button>
            <Button
              className="secondary"
              disabled={generate.isPending}
              onClick={() => request(true)}
            >
              重新生成
            </Button>
          </div>
        </div>
        {!body.outline_version_id && (
          <div className="alert" role="status">
            还没有已确认的提纲：全文必须基于确认过的提纲版本，请先在「提纲」分区确认一版。
          </div>
        )}
        <TaskStatusBanner
          status={body.task_status}
          queuedText="全文生成已排队，等待 worker 领取"
          runningText="正在生成全文…"
          doneText={
            versions.length > 0
              ? `生成完成：共 ${versions.length} 个版本`
              : "生成完成"
          }
          failedText="上次生成失败"
          retryLabel="重新生成"
          busy={generate.isPending}
          onRetry={
            body.task_status === "failed" ? () => request(true) : undefined
          }
        />
        {generate.isError && (
          <div className="field-error" role="alert">
            {generate.error.message}
          </div>
        )}
        {body.has_blocking && (
          <div className="alert" role="alert">
            {`还有 ${body.blocking_count} 条阻塞缺口未处理：可以继续生成，但按现有资料写主线会有风险，成稿里的相关结论需要人工核对。`}
          </div>
        )}
        {body.has_unconfirmed_changes && (
          <div className="alert" role="status">
            {
              "已确认的提纲不是最新版本：全文仍按已确认的那一版生成；改过结构后请重新确认提纲。"
            }
          </div>
        )}
        {versions.length === 0 ? (
          <EmptyState
            title="还没有全文"
            description="点「生成全文」按已确认提纲与已核对事实写一版；生成后可在本页阅读并逐条核对引用。"
          />
        ) : (
          <ul className="related-list">
            {versions.map((version) => (
              <li key={version.id} className="related-item">
                <div className="related-item-shared">
                  <Badge>第 {version.version_number} 版</Badge>
                  <Badge
                    tone={version.kind === "edited" ? "success" : "warning"}
                  >
                    {KIND_LABELS[version.kind] ?? version.kind}
                  </Badge>
                  <Badge tone="neutral">
                    {STATUS_LABELS[version.status] ?? version.status}
                  </Badge>
                  <span className="muted">
                    {formatTime(version.saved_at ?? version.created_at)}
                    {version.is_latest && " · 最新"}
                  </span>
                </div>
                <strong>{version.title || "（无标题）"}</strong>
                <span className="muted">
                  {version.source_count} 个来源
                  {version.source_count === 1 && " · 单一来源"}
                </span>
                {counterNotes(version.counters).length > 0 && (
                  <span className="field-error">
                    {counterNotes(version.counters).join(" · ")}
                  </span>
                )}
                <div className="toolbar">
                  <Button
                    className="secondary"
                    onClick={() => setSelectedId(version.id)}
                  >
                    查看这一版
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {selected && (
        <Card className="card-pad">
          <div className="toolbar">
            <h2>第 {selected.version_number} 版</h2>
            <span className="muted">
              {KIND_LABELS[selected.kind] ?? selected.kind} ·{" "}
              {STATUS_LABELS[selected.status] ?? selected.status} · 提纲 v
              {selected.outline_version_id.slice(0, 8)}
            </span>
          </div>
          {preview.isLoading && <div className="loading">正在渲染正文…</div>}
          {preview.isError && (
            <ErrorState
              message={preview.error?.message ?? "预览加载失败"}
              onRetry={() => void preview.refetch()}
            />
          )}
          {preview.data && (
            <>
              {preview.data.single_source && (
                <div className="alert" role="status">
                  本篇只有一个来源：不要把单一来源写成多方共识，发布前请人工确认。
                </div>
              )}
              {preview.data.pending_notes.length > 0 && (
                <div className="alert" role="status">
                  {`写这篇时仍有 ${preview.data.pending_notes.length} 条未处理的缺口/分歧，正文里的相关表述需要人工核对。`}
                </div>
              )}
              <ArticleView preview={preview.data} />
              <p className="muted">
                生成信息：{selected.model ?? "（无模型）"}
                {selected.prompt_version &&
                  ` · 提示词 ${selected.prompt_version}`}
                {` · 来源 ${preview.data.source_count} 个`}
              </p>
            </>
          )}
        </Card>
      )}
    </>
  );
}
