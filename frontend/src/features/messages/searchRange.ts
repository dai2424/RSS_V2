/** 消息检索的时间范围：搜索默认最近一周，浏览默认全部时间。 */

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
export function rangeLabel(range: RangeValue): string {
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
