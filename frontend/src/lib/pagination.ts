/** 列表分页的纯计算：每页条数、页码列表与偏移换算。
 *
 * 分页状态（offset 与 size）保存在 URL 上，这里只做计算，不碰路由、请求和界面。
 */

/** 每页条数可选值；不超过列表接口 limit 的上限（100）。 */
export const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

export const DEFAULT_PAGE_SIZE = 25;

/** 页码列表里省略号的占位标记。 */
export const ELLIPSIS = "ellipsis";

export type PageItem = number | typeof ELLIPSIS;

/** URL 上的 size 可能是手写或过期的值：不认识的取值回落到默认每页条数。 */
export function normalizeSize(value: number | null): number {
  if (value === null || !PAGE_SIZE_OPTIONS.some((option) => option === value)) {
    return DEFAULT_PAGE_SIZE;
  }
  return value;
}

/** 总页数；没有记录时也算 1 页，避免界面出现「第 0 页」。 */
export function pageCountOf(total: number, size: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, size)));
}

/** 偏移换算成页码，页码从 1 开始。 */
export function pageOfOffset(offset: number, size: number): number {
  return Math.floor(Math.max(0, offset) / Math.max(1, size)) + 1;
}

/** 页码换算成偏移，页码从 1 开始。 */
export function offsetFor(page: number, size: number): number {
  return Math.max(0, page - 1) * Math.max(1, size);
}

/** 切换每页条数：保留当前页码并按新页大小重算，超出新的末页时夹紧到最后一页。 */
export function sizeChangeOffset(
  offset: number,
  size: number,
  nextSize: number,
  total: number,
): number {
  const page = Math.min(
    pageOfOffset(offset, size),
    pageCountOf(total, nextSize),
  );
  return offsetFor(page, nextSize);
}

/**
 * 页码列表：页数不超过 7 时全部列出，否则固定 7 个格子，中间用省略号折叠。
 *
 * 规则与 Element UI 的 pager 一致：当前页在中间时是 `1 … cur-1 cur cur+1 … last`；
 * 靠近首页（前 4 页内）补足前 5 页，靠近末页则补足最后 5 页，避免页码随翻页跳动。
 */
export function pageItems(page: number, pageCount: number): PageItem[] {
  if (pageCount <= 7) {
    return range(1, pageCount);
  }
  const showPrevMore = page > 4;
  const showNextMore = page < pageCount - 3;
  if (!showPrevMore) {
    return [...range(1, 5), ELLIPSIS, pageCount];
  }
  if (!showNextMore) {
    return [1, ELLIPSIS, ...range(pageCount - 4, pageCount)];
  }
  return [1, ELLIPSIS, ...range(page - 1, page + 1), ELLIPSIS, pageCount];
}

function range(from: number, to: number): number[] {
  const items: number[] = [];
  for (let value = from; value <= to; value += 1) {
    items.push(value);
  }
  return items;
}
