import type { components } from "../../api/generated";
import { Card, EmptyState, ErrorState } from "../../components/ui";
import { Pagination } from "../../components/Pagination";
import { KeywordFilters } from "./KeywordFilters";
import { KeywordTable } from "./KeywordTable";
import { useSetKeywordPin } from "./useKeywordsData";

type Entry = components["schemas"]["KeywordEntryResponse"];

/** 词表：按出现次数排序的规范词；勾选若干行后可以合并，开关标记常驻。 */
export function KeywordVocabulary({
  items,
  total,
  totalTerms,
  q,
  kind,
  scope,
  minCount,
  pinned,
  hotDays,
  hotMinMentions,
  offset,
  size,
  loading,
  paging,
  error,
  onRetry,
  onFilter,
  onPage,
  onSize,
  onClear,
  selection,
  onToggle,
}: {
  items: Entry[];
  total: number;
  totalTerms: number;
  q: string;
  kind: string;
  scope: string;
  minCount: number | null;
  pinned: boolean;
  hotDays: number;
  hotMinMentions: number;
  offset: number;
  size: number;
  loading: boolean;
  paging: boolean;
  error?: string;
  onRetry: () => void;
  onFilter: (key: string, value: string) => void;
  onPage: (offset: number) => void;
  onSize: (size: number) => void;
  onClear: () => void;
  selection: Set<string>;
  onToggle: (item: Entry) => void;
}) {
  const pin = useSetKeywordPin();
  const filtered = Boolean(
    q || kind || scope !== "hot" || minCount !== null || pinned,
  );
  return (
    <Card className="card-pad">
      <KeywordFilters
        q={q}
        kind={kind}
        scope={scope}
        minCount={minCount}
        pinned={pinned}
        hotDays={hotDays}
        hotMinMentions={hotMinMentions}
        filtered={filtered}
        onFilter={onFilter}
        onClear={onClear}
      />
      {loading && <div className="loading">正在加载词表…</div>}
      {error && <ErrorState message={error} onRetry={onRetry} />}
      {!loading && !error && items.length === 0 && (
        <EmptyState
          title={
            total > 0 ? "这一页没有内容" : emptyTitle(filtered, totalTerms)
          }
          description={
            total > 0
              ? `当前共 ${total} 个词，这个偏移已经越过末尾。`
              : emptyHint(filtered, scope)
          }
          action={
            total > 0 ? (
              <button
                type="button"
                className="button secondary"
                onClick={() => onPage(0)}
              >
                回到第一页
              </button>
            ) : undefined
          }
        />
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <KeywordTable
            items={items}
            selection={selection}
            pinPending={pin.isPending}
            onToggle={onToggle}
            onTogglePin={(item) =>
              pin.mutate({ key: item.key, pinned: !item.pinned })
            }
          />
          <Pagination
            total={total}
            offset={offset}
            size={size}
            unit="个词"
            disabled={paging}
            onPage={onPage}
            onSize={onSize}
          />
        </>
      )}
    </Card>
  );
}

/** 空态要区分"没有词""热词还没达标""筛选无结果"，否则用户会以为数据没进来。 */
function emptyTitle(filtered: boolean, totalTerms: number): string {
  if (filtered) return "没有匹配的关键词";
  if (totalTerms > 0) return "这一周还没有达标的热词";
  return "还没有关键词";
}

function emptyHint(filtered: boolean, scope: string): string {
  if (filtered) return "换一个写法或放宽筛选条件试试。";
  if (scope === "hot")
    return "热词要求窗口内出现次数达到门槛；切到「全量」可以看历史词与孤词，或把最小出现次数调低。";
  return "关键词由内容加工产出，可以在页面右上角回填存量消息。";
}
