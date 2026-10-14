import { useState } from "react";
import type { components } from "../../api/generated";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
} from "../../components/ui";
import { MergeDialog } from "./KeywordMerges";
import { useKeywordSuggestions } from "./useKeywordsData";

type Suggestion = components["schemas"]["KeywordSuggestionResponse"];

/** 合并建议：模型给候选组，人工确认后才合并；批次由独立 worker 执行，进度在这里显示。 */
export function KeywordSuggestions() {
  const { list, pending, queued, failed, total, scanned, enqueue, dismiss } =
    useKeywordSuggestions();
  const [adopting, setAdopting] = useState<Suggestion | null>(null);
  const items = list.data?.items ?? [];
  const busy = enqueue.isPending;
  const remaining = Math.max(0, total - scanned);

  return (
    <Card className="card-pad">
      <div className="toolbar">
        <h2>合并建议</h2>
        <span className="muted">
          待确认 {pending} 组；已看 {scanned}/{total} 个词
          {queued > 0 ? `，${queued} 个批次在排队或运行` : ""}
          {failed > 0 ? `，${failed} 个批次失败（可在任务页重试）` : ""}
        </span>
        <Button
          className="secondary"
          disabled={busy || remaining === 0}
          title={
            remaining === 0
              ? "词表都看过了"
              : "排一个批次（只含没看过的词），约需几十秒"
          }
          onClick={() => enqueue.mutate(1)}
        >
          {busy ? "排队中…" : "生成一批"}
        </Button>
        <Button
          disabled={busy || remaining === 0}
          title={
            remaining === 0
              ? "词表都看过了"
              : "把没看过的词按批次大小排完，由 worker 逐批执行"
          }
          onClick={() => enqueue.mutate(50)}
        >
          排队扫完剩余
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
          description={
            remaining > 0
              ? "点「生成一批」把还没看过的词交给模型找疑似同指组；看过的词不会重扫，忽略过的组也不会再出现。"
              : "词表都看过了，没有更多可建议的词；等有新词出现再来。"
          }
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
