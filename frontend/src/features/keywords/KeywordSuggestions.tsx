import { useState } from "react";
import type { components } from "../../api/generated";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
} from "../../components/ui";
import { formatTime } from "../../lib/display";
import { MergeDialog } from "./KeywordMerges";
import { useKeywordSuggestions } from "./useKeywordsData";

type Suggestion = components["schemas"]["KeywordSuggestionResponse"];

/** 合并建议：模型给候选组，人工确认后才合并；批次由独立 worker 执行，进度在这里显示。 */
export function KeywordSuggestions() {
  const {
    list,
    pending,
    queued,
    failed,
    fresh,
    lastScannedAt,
    enqueue,
    dismiss,
  } = useKeywordSuggestions();
  const [adopting, setAdopting] = useState<Suggestion | null>(null);
  const items = list.data?.items ?? [];
  const busy = enqueue.isPending;

  return (
    <Card className="card-pad">
      <div className="toolbar">
        <h2>合并建议</h2>
        <span className="muted">
          待确认 {pending} 组；新词 {fresh} 个
          {lastScannedAt
            ? ` · 上次扫描 ${formatTime(lastScannedAt)}`
            : " · 还没扫过"}
          {queued > 0 ? ` · ${queued} 个批次在排队或运行` : ""}
          {failed > 0 ? ` · ${failed} 个批次失败（可在任务页重试）` : ""}
        </span>
        <Button
          className="secondary"
          disabled={busy}
          title="排一个批次（约需几十秒）：新词优先，老词按最久没看过排队重扫"
          onClick={() => enqueue.mutate(1)}
        >
          {busy ? "排队中…" : "扫一批"}
        </Button>
        <Button
          disabled={busy}
          title="一次排十批，由 worker 逐批执行；扫描是滚动的，不存在扫完的终态"
          onClick={() => enqueue.mutate(10)}
        >
          接着扫 10 批
        </Button>
      </div>
      {enqueue.isError && (
        <div className="alert-error">{enqueue.error.message}</div>
      )}
      {list.isLoading && <div className="loading">正在加载建议…</div>}
      {list.isError && (
        <ErrorState
          message={list.error.message}
          onRetry={() => void list.refetch()}
        />
      )}
      {!list.isLoading && !list.isError && items.length === 0 && (
        <EmptyState
          title="没有待确认的建议"
          description="点「扫一批」把词表里的写法交给模型找疑似同指组：新词优先，老词按最久没看过轮着重看，忽略过的组不会再出现。"
        />
      )}
      {items.length > 0 && (
        <ul className="stack">
          {items.map((item) => (
            <li key={item.id} className="row-actions">
              <span className="badge-row">
                {item.members.map((member) => (
                  <Badge key={member.key} tone="neutral">
                    {member.text}
                  </Badge>
                ))}
              </span>
              <span className="muted">
                保留 {item.target_text}
                {item.rationale ? `：${item.rationale}` : ""}
              </span>
              <Button onClick={() => setAdopting(item)}>采纳</Button>
              <Button
                className="secondary"
                disabled={dismiss.isPending}
                onClick={() => dismiss.mutate(item.id)}
              >
                忽略
              </Button>
            </li>
          ))}
        </ul>
      )}
      {adopting && (
        <SuggestionMergeDialog
          suggestion={adopting}
          onClose={() => setAdopting(null)}
        />
      )}
    </Card>
  );
}

/** 采纳弹层：成员与目标都来自建议，执行仍走既有合并端点（含影响面与撤销）。 */
function SuggestionMergeDialog({
  suggestion,
  onClose,
}: {
  suggestion: Suggestion;
  onClose: () => void;
}) {
  const { accept } = useKeywordSuggestions();
  const members = suggestion.members.map((member) => ({
    key: member.key,
    text: member.text,
    mentions: 0,
  }));
  // 目标词排在第一位：弹层默认保留它，用户可以改成组内其它写法。
  const ordered = [
    ...members.filter((member) => member.key === suggestion.target_key),
    ...members.filter((member) => member.key !== suggestion.target_key),
  ];
  return (
    <MergeDialog
      open
      selected={ordered}
      onClose={onClose}
      onMerged={(mergeId) => accept.mutate({ id: suggestion.id, mergeId })}
    />
  );
}
