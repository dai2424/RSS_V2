import { CUSTOM_VALUE, boundsOf, type CustomRange } from "./searchRange";

/** 选题发现的时间窗口预设：没有「全部时间」——不限时间的全量扫描不是产品口径。 */
export const DISCOVERY_RANGES = ["7d", "30d", "90d"] as const;

/** 默认窗口：最近 7 天（产品口径）。 */
export const DEFAULT_DISCOVERY_RANGE = "7d";

const DAY = 86400;

/**
 * 把界面上的范围选择换算成接口要的 UTC 秒窗口。
 *
 * - 预设：`[now - N 天, now)`，与后端默认窗口同一口径；
 * - 指定日期：按上海时区自然日取 `[起点 0 点, 终点 23:59:59]`。
 *
 * 窗口只在点击「发现选题」时计算一次并随请求发出，服务端把它写进批次，
 * 因此界面上显示的范围与真正生效的范围永远一致。
 */
export function discoveryWindow(
  range: string,
  custom: CustomRange,
  now: number,
): { window_start: number; window_end: number } {
  if (range === CUSTOM_VALUE) {
    const bounds = boundsOf(custom);
    return { window_start: bounds.since, window_end: bounds.until };
  }
  const days = range === "90d" ? 90 : range === "30d" ? 30 : 7;
  return { window_start: now - days * DAY, window_end: now };
}

/** 范围文案：用于按钮区与结果里的「本次生效范围」提示。 */
export function discoveryRangeLabel(range: string): string {
  if (range === "90d") return "最近 90 天";
  if (range === "30d") return "最近 30 天";
  if (range === CUSTOM_VALUE) return "指定日期";
  return "最近 7 天";
}
