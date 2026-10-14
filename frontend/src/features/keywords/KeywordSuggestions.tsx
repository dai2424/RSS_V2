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

/** 合并建议：模型给候选组，人工确认后才合并；采纳复用既有合并弹层。 */
export function KeywordSuggestions() {
  const { list, pending, generate, dismiss } = useKeywordSuggestions();
  const [adopting, setAdopting] = useState<Suggestion | null>(null);
  const items = list.data?.items ?? [];
  const batch = generate.data;

  return (
    <Card className="card-pad">
      <div className="toolbar">
        <h2>合并建议</h2>
        <span className="muted">
          待确认 {pending} 组；每批 20
          个词、约需半分钟，模型只给候选，合并仍由你确认
        </span>
        <Button
          className="secondary"
          disabled={generate.isPending}
          onClick={() => generate.mutate(undefined)}
        >
          {generate.isPending ? "生成中…" : "生成一批建议"}
        </Button>
      </div>
      {batch && (
        <p className="muted">
          {batch.size > 0
            ? `本批 ${batch.size} 个词，得到 ${batch.groups} 组建议${
                batch.dropped > 0 ? `，丢弃 ${batch.dropped} 组` : ""
              }。`
            : "词表都看过了，没有更多可建议的词。"}
        </p>
      )}
      {generate.isError && (
        <div className="alert-error">{generate.error.message}</div>
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
          description="点「生成一批建议」把词表交给模型找疑似同指组；已经忽略的组不会再出现。"
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
