import type { components } from "../../api/generated";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
} from "../../components/ui";
import { usePinSuggestions } from "./useKeywordsData";

type PinSuggestion = components["schemas"]["PinSuggestionResponse"];

/** 常驻建议：候选按跨周出现算出，模型只做挑选，采纳后才写常驻。 */
export function PinSuggestions() {
  const { list, suggest, resolve, pending } = usePinSuggestions();
  const items: PinSuggestion[] = list.data?.items ?? [];
  const busy = suggest.isPending || resolve.isPending;

  return (
    <Card className="card-pad">
      <div className="toolbar">
        <h2>常驻建议</h2>
        <span className="muted">
          待确认 {pending}{" "}
          个；候选取自"最近四周里至少三周出现"的词，采纳后才常驻
        </span>
        <Button
          className="secondary"
          disabled={busy}
          title="让模型从跨周候选里挑出值得常驻的词（同步调用，约需几秒）"
          onClick={() => suggest.mutate()}
        >
          {suggest.isPending ? "推荐中…" : "推荐常驻词"}
        </Button>
      </div>
      {suggest.isError && (
        <div className="alert-error">{suggest.error.message}</div>
      )}
      {list.isLoading && <div className="loading">正在加载常驻建议…</div>}
      {list.isError && (
        <ErrorState
          message={list.error.message}
          onRetry={() => void list.refetch()}
        />
      )}
      {!list.isLoading && !list.isError && items.length === 0 && (
        <EmptyState
          title="没有待确认的常驻建议"
          description="点「推荐常驻词」让模型从跨周反复出现的词里挑；样本还不够时会明说，而不是硬凑。"
        />
      )}
      {items.length > 0 && (
        <ul className="stack">
          {items.map((item) => (
            <li key={item.key} className="row-actions">
              <Badge tone="neutral">{item.text}</Badge>
              <span className="muted">
                跨 {item.weeks} 周{item.rationale ? `：${item.rationale}` : ""}
              </span>
              <Button
                disabled={resolve.isPending}
                onClick={() =>
                  resolve.mutate({ key: item.key, accepted: true })
                }
              >
                设为常驻
              </Button>
              <Button
                className="secondary"
                disabled={resolve.isPending}
                onClick={() =>
                  resolve.mutate({ key: item.key, accepted: false })
                }
              >
                忽略
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
