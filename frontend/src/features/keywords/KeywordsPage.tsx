import { useState } from "react";
import {
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { KeywordCharts } from "./KeywordCharts";
import { KeywordMaintenance } from "./KeywordMaintenance";
import { KeywordMetrics } from "./KeywordMetrics";
import { KeywordSuggestions } from "./KeywordSuggestions";
import { MergeDialog, MergeHistory } from "./KeywordMerges";
import { KeywordVocabulary } from "./KeywordVocabulary";
import { useKeywordOverview, useKeywordVocabulary } from "./useKeywordsData";

/** 关键词管理：先看规模与分布，再按需要收拾词表与回填存量。 */
export function KeywordsPage() {
  const overview = useKeywordOverview();
  const vocabulary = useKeywordVocabulary();
  const [selection, setSelection] = useState<
    Map<string, { key: string; text: string; mentions: number }>
  >(new Map());
  const [mergeOpen, setMergeOpen] = useState(false);

  const toggle = (item: { key: string; text: string; mentions: number }) => {
    setSelection((current) => {
      const next = new Map(current);
      if (next.has(item.key)) next.delete(item.key);
      else next.set(item.key, item);
      return next;
    });
  };

  if (overview.isLoading)
    return <div className="loading">正在加载关键词统计…</div>;
  if (overview.isError)
    return (
      <ErrorState
        message={overview.error.message}
        onRetry={() => void overview.refetch()}
      />
    );
  const data = overview.data;
  if (!data)
    return (
      <EmptyState
        title="没有关键词数据"
        description="先在消息里生成摘要，关键词随之产出。"
      />
    );

  const selected = [...selection.values()];
  return (
    <div className="page page-wide">
      <PageHeader
        title="关键词"
        description="关键词是检索与后续文章聚类的地基：这里看它的规模与分布，并把同指的写法合并成一个词条。"
        action={<KeywordMaintenance pending={data.pending} />}
      />
      <KeywordMetrics overview={data} />
      <KeywordCharts overview={data} />
      {selection.size > 0 && (
        <div className="toolbar">
          <span className="muted">已选择 {selection.size} 个词</span>
          <Button
            disabled={selection.size < 2}
            onClick={() => setMergeOpen(true)}
            title={selection.size < 2 ? "至少选择两个同指写法" : undefined}
          >
            合并选中的词
          </Button>
          <Button className="secondary" onClick={() => setSelection(new Map())}>
            清空选择
          </Button>
          {selection.size === 1 && (
            <span className="muted">至少选择两个词才能合并。</span>
          )}
        </div>
      )}
      <KeywordVocabulary
        items={vocabulary.list.data?.items ?? []}
        total={vocabulary.list.data?.total ?? 0}
        totalTerms={data.terms}
        q={vocabulary.q}
        kind={vocabulary.kind}
        minCount={vocabulary.minCount}
        pinned={vocabulary.pinned}
        offset={vocabulary.offset}
        size={vocabulary.size}
        loading={vocabulary.list.isLoading}
        paging={vocabulary.list.isPlaceholderData}
        error={
          vocabulary.list.isError ? vocabulary.list.error.message : undefined
        }
        onRetry={() => void vocabulary.list.refetch()}
        onFilter={vocabulary.setFilter}
        onPage={vocabulary.setPageOffset}
        onSize={(next) =>
          vocabulary.setSize(next, vocabulary.list.data?.total ?? 0)
        }
        onClear={vocabulary.clear}
        selection={new Set(selection.keys())}
        onToggle={(item) =>
          toggle({ key: item.key, text: item.text, mentions: item.mentions })
        }
      />
      <MergeDialog
        open={mergeOpen}
        selected={selected}
        onClose={() => {
          setMergeOpen(false);
          setSelection(new Map());
        }}
      />
      <KeywordSuggestions />
      <MergeHistory />
    </div>
  );
}
