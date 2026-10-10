import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { components } from "../../api/generated";

type Overview = components["schemas"]["KeywordOverviewResponse"];

const SERIES_1 = "var(--chart-series-1)";
const SERIES_2 = "var(--chart-series-2)";

/** 图表外框：文字摘要 + 同数据表格兜底，屏幕阅读器与色觉差异读者都能拿到同样的信息。 */
function ChartFrame({
  title,
  summary,
  label,
  children,
  table,
}: {
  title: string;
  summary: string;
  label: string;
  children: ReactNode;
  table: ReactNode;
}) {
  return (
    <figure className="keyword-chart">
      <figcaption>
        <strong>{title}</strong>
      </figcaption>
      <div role="img" aria-label={label} style={{ height: 220, marginTop: 8 }}>
        {children}
      </div>
      <figcaption>{summary}</figcaption>
      <details>
        <summary>数据表</summary>
        {table}
      </details>
    </figure>
  );
}

/** 近 N 天趋势：词位数与当天新增词。 */
function TrendChart({ overview }: { overview: Overview }) {
  const trend = overview.trend;
  const total = trend.reduce((sum, point) => sum + point.mentions, 0);
  const peak = trend.reduce((best, point) =>
    point.mentions > best.mentions ? point : best,
  );
  return (
    <ChartFrame
      title="每日产出"
      label={`近 ${trend.length} 天的每日词位数与新增词`}
      summary={
        trend.length === 0
          ? "还没有可统计的产出。"
          : `近 ${trend.length} 天共 ${total} 个词位，峰值出现在 ${peak.day}（${peak.mentions} 个）。`
      }
      table={
        <table>
          <thead>
            <tr>
              <th scope="col">日期</th>
              <th scope="col">词位数</th>
              <th scope="col">新增词</th>
            </tr>
          </thead>
          <tbody>
            {trend.map((point) => (
              <tr key={point.day}>
                <td>{point.day}</td>
                <td>{point.mentions}</td>
                <td>{point.new_terms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={trend}
          margin={{ top: 8, right: 8, bottom: 0, left: -20 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 12 }}
            tickFormatter={(v: string) => v.slice(5)}
          />
          <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="mentions"
            name="词位数"
            stroke={SERIES_1}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="new_terms"
            name="新增词"
            stroke={SERIES_2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** 分类分布：类型构成、长尾分档、来源 Top。三者都是"标签 + 数值"，用横向条形。 */
function CategoryChart({
  title,
  summary,
  data,
  unit,
  highlight,
}: {
  title: string;
  summary: string;
  data: { label: string; value: number }[];
  unit: string;
  highlight: boolean;
}) {
  const top = data.reduce(
    (best, item) => (item.value > best.value ? item : best),
    data[0],
  );
  return (
    <ChartFrame
      title={title}
      label={`${title}：${data.map((item) => `${item.label} ${item.value}`).join("，")}`}
      summary={summary}
      table={
        <table>
          <thead>
            <tr>
              <th scope="col">标签</th>
              <th scope="col">{unit}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((item) => (
              <tr key={item.label}>
                <td>{item.label}</td>
                <td>{item.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(var(--border))"
            horizontal={false}
          />
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12 }} />
          <YAxis
            type="category"
            dataKey="label"
            width={96}
            tick={{ fontSize: 12 }}
          />
          <Tooltip />
          <Bar dataKey="value" name={unit} fill={SERIES_1}>
            {data.map((item) => (
              <Cell
                key={item.label}
                fill={highlight && item === top ? SERIES_1 : SERIES_2}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** 概览图表区：四张图分别回答"产出节奏""词是什么类型""长尾多长""谁在贡献"。 */
export function KeywordCharts({ overview }: { overview: Overview }) {
  const kinds = overview.kinds.map((stat) => ({
    label: kindLabel(stat.kind),
    value: stat.terms,
  }));
  const sources = overview.sources.map((stat) => ({
    label: stat.source_name,
    value: stat.mentions,
  }));
  const longTail = overview.long_tail.map((bucket) => ({
    label: bucket.label,
    value: bucket.terms,
  }));
  return (
    <div className="keyword-charts">
      <TrendChart overview={overview} />
      <CategoryChart
        title="类型构成"
        unit="规范词数"
        data={kinds}
        highlight
        summary={`实体 ${kinds[0]?.value ?? 0} 个、主题 ${kinds[1]?.value ?? 0} 个、事件 ${kinds[2]?.value ?? 0} 个。`}
      />
      <CategoryChart
        title="长尾分布"
        unit="规范词数"
        data={longTail}
        highlight
        summary={`只出现一次的写法有 ${longTail[0]?.value ?? 0} 个，占 ${percent(
          longTail[0]?.value ?? 0,
          overview.terms,
        )}。`}
      />
      <CategoryChart
        title="来源分布"
        unit="词位数"
        data={sources}
        highlight={false}
        summary={
          sources.length === 0
            ? "还没有来源贡献关键词。"
            : `词位数最多的来源是 ${sources[0].label}（${sources[0].value} 个），共 ${sources.length} 个来源有产出。`
        }
      />
    </div>
  );
}

function kindLabel(kind: string): string {
  if (kind === "entity") return "实体";
  if (kind === "topic") return "主题";
  return "事件";
}

/** 占比文案；分母为 0 时不给百分比，避免出现 NaN。 */
export function percent(value: number, total: number): string {
  if (!total) return "—";
  return `${((value / total) * 100).toFixed(1)}%`;
}
