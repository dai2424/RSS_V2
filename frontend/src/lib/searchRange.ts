/** 列表检索的时间范围：消息页搜索默认最近一周、浏览默认全部；任务页默认全部。 */

/** seconds 为 0 表示不限时间。 */
export const RANGE_OPTIONS = [
  { value: "7d", label: "近 7 天", seconds: 7 * 86400 },
  { value: "30d", label: "近 30 天", seconds: 30 * 86400 },
  { value: "90d", label: "近 90 天", seconds: 90 * 86400 },
  { value: "all", label: "全部时间", seconds: 0 },
] as const;

export type RangeValue = (typeof RANGE_OPTIONS)[number]["value"];

/**
 * 当前生效的时间范围。
 *
 * 规则只依赖 URL：显式选过就沿用；没选过时搜索默认近 7 天、浏览默认全部时间。
 * 时间窗只收敛结果范围，不降低扫描成本——扫描由 messages 驱动，表达式用不上索引。
 */
export function rangeOf(
  selected: string | null,
  searching: boolean,
): RangeValue {
  const known = RANGE_OPTIONS.find((option) => option.value === selected);
  if (known) return known.value;
  return searching ? "7d" : "all";
}

/** 范围的中文说明，用于空态与筛选提示。 */
export function rangeLabel(range: RangeValue | typeof CUSTOM_VALUE): string {
  if (range === CUSTOM_VALUE) return "指定日期";
  return (
    RANGE_OPTIONS.find((option) => option.value === range)?.label ?? "全部时间"
  );
}

/** 把范围换算成 since（UTC 秒）；不限范围时返回 undefined。 */
export function sinceOf(
  range: RangeValue,
  nowSeconds: number,
): number | undefined {
  const option = RANGE_OPTIONS.find((item) => item.value === range);
  if (!option || option.seconds === 0) return undefined;
  return nowSeconds - option.seconds;
}

/** 自定义日期范围在 URL 上占用的参数名；存在时优先于预设范围。 */
export const CUSTOM_VALUE = "custom";

/** 自定义日期范围：两个 YYYY-MM-DD，按上海时区的自然日理解。 */
export type CustomRange = { from: string; to: string };

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** 某个时刻在上海时区的日期（UTC+8 无夏令时，不需要时区库）。 */
function shanghaiDate(epochMs: number): string {
  return new Date(epochMs + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

/** 上海时区的今天。 */
export function shanghaiToday(now: Date = new Date()): string {
  return shanghaiDate(now.getTime());
}

/** 自定义范围的默认值：最近 7 天（含今天），与"近 7 天"预设对齐。 */
export function defaultCustomRange(
  today: string = shanghaiToday(),
): CustomRange {
  const startMs = Date.parse(`${today}T00:00:00+08:00`) - 6 * 86400 * 1000;
  return { from: shanghaiDate(startMs), to: today };
}

/**
 * 从 URL 读自定义日期范围；缺一个、格式不对或顺序颠倒都按没有处理。
 *
 * 顺序颠倒时交换而不是丢弃：用户意图是一个区间，不该因为点错先后就退化成不过滤。
 * 界面上的日期选择会主动纠正顺序，这里只是兜底。
 */
export function customRangeOf(
  from: string | null,
  to: string | null,
): CustomRange | null {
  const start = from ?? "";
  const end = to ?? "";
  if (!DATE_PATTERN.test(start) || !DATE_PATTERN.test(end)) return null;
  return start <= end ? { from: start, to: end } : { from: end, to: start };
}

/** 把日期范围换算成 UTC 秒边界：起点当天 0 点、终点当天 23:59:59（上海时区）。 */
export function boundsOf(range: CustomRange): { since: number; until: number } {
  return {
    since: Math.floor(Date.parse(`${range.from}T00:00:00+08:00`) / 1000),
    until: Math.floor(Date.parse(`${range.to}T23:59:59+08:00`) / 1000),
  };
}

/** 解析后的时间范围：请求用的 since/until、下拉框的取值，以及自定义范围本身。 */
export type ResolvedRange = {
  value: RangeValue | typeof CUSTOM_VALUE; // 下拉框显示的取值
  since?: number; // 下界，UTC 秒
  until?: number; // 上界，UTC 秒
  custom: CustomRange | null; // 自定义日期范围；用预设时为空
};

/**
 * 把 URL 参数换算成请求参数：自定义日期优先于预设范围。
 *
 * 两个列表页共用，避免"消息页按自定义日期、任务页却按预设"这种口径分叉。
 */
export function resolveRange(
  params: { range: string | null; from: string | null; to: string | null },
  searching: boolean,
  nowSeconds: number,
): ResolvedRange {
  const custom = customRangeOf(params.from, params.to);
  if (custom) {
    const bounds = boundsOf(custom);
    return {
      value: CUSTOM_VALUE,
      since: bounds.since,
      until: bounds.until,
      custom,
    };
  }
  const value = rangeOf(params.range, searching);
  return { value, since: sinceOf(value, nowSeconds), custom: null };
}

/** 改一端日期；顺序点反了就把另一端拉过来，始终保持有效区间。 */
export function withDate(
  range: CustomRange,
  key: keyof CustomRange,
  value: string,
): CustomRange {
  const next = { ...range, [key]: value };
  if (next.from > next.to) {
    return key === "from"
      ? { from: next.from, to: next.from }
      : { from: next.to, to: next.to };
  }
  return next;
}
