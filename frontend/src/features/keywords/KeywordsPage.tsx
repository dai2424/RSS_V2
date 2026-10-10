import { useState } from "react";
import {
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { KeywordCharts, percent } from "./KeywordCharts";
import { KeywordMaintenance } from "./KeywordMaintenance";
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
      <dl className="keyword-metrics">
        <Metric label="规范词数" value={data.terms} />
        <Metric label="词位总数" value={data.mentions} />
        <Metric
          label="只出现一次"
          value={`${data.singletons}（${percent(data.singletons, data.terms)}）`}
        />
        <Metric label="平均每条" value={`${data.average_per_message} 个`} />
        <Metric
          label="已加工消息"
          value={`${data.enriched}/${data.messages}`}
          hint={`待回填 ${data.pending} 条`}
        />
        <Metric
          label="已并入写法"
          value={`${data.aliases}（合并 ${data.merges} 次）`}
        />
        <Metric
          label="加工任务"
          value={`排队 ${data.tasks_queued} · 运行 ${data.tasks_running}`}
          hint={`完成 ${data.tasks_succeeded} · 失败 ${data.tasks_failed}`}
        />
      </dl>
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
      <MergeHistory />
    </div>
  );
}

/** 概览指标卡；hint 只在有补充说明时出现。 */
function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="keyword-metric">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {hint && <span className="muted">{hint}</span>}
    </div>
  );
}
