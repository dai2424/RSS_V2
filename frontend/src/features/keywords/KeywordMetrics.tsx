import type { components } from "../../api/generated";
import { percent } from "./KeywordCharts";

type Overview = components["schemas"]["KeywordOverviewResponse"];

/** 概览指标卡：规模、复用与常驻一目了然；口径随卡片一起说明。 */
export function KeywordMetrics({ overview }: { overview: Overview }) {
  return (
    <dl className="keyword-metrics">
      <Metric label="规范词数" value={overview.terms} />
      <Metric label="词位总数" value={overview.mentions} />
      <Metric
        label="只出现一次"
        value={`${overview.singletons}（${percent(overview.singletons, overview.terms)}）`}
      />
      <Metric label="平均每条" value={`${overview.average_per_message} 个`} />
      <Metric
        label="已加工消息"
        value={`${overview.enriched}/${overview.messages}`}
        hint={`待回填 ${overview.pending} 条`}
      />
      <Metric
        label="已并入写法"
        value={`${overview.aliases}（合并 ${overview.merges} 次）`}
      />
      <Metric
        label="词表复用率"
        value={
          overview.reuse_judged > 0
            ? percent(overview.reused, overview.reuse_judged)
            : "—"
        }
        hint={
          overview.reuse_judged > 0
            ? `按 ${overview.reuse_judged} 个可判定词位；旧结果不参与`
            : "还没有可判定的加工结果"
        }
      />
      <Metric
        label="常驻词"
        value={overview.pinned}
        hint="注入提示词时必然出现"
      />
      <Metric
        label="词表版本"
        value={`v${overview.vocab_revision}`}
        hint="注入内容变化时递增"
      />
      <Metric
        label="加工任务"
        value={`排队 ${overview.tasks_queued} · 运行 ${overview.tasks_running}`}
        hint={`完成 ${overview.tasks_succeeded} · 失败 ${overview.tasks_failed}`}
      />
    </dl>
  );
}

/** 指标卡；hint 只在有补充说明时出现。 */
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
