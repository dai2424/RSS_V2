import { useSearchParams } from "react-router-dom";
import {
  DEFAULT_PAGE_SIZE,
  normalizeSize,
  sizeChangeOffset,
} from "./pagination";

/**
 * 分页状态的 URL 读写：offset 与 size 都在查询串上，刷新、从详情返回和分享链接都能恢复。
 *
 * 切页用 push（浏览器可以后退回上一页）；筛选条件由各页面自己用 replace 改，
 * 改筛选时会删掉 offset 回到第一页，size 保持用户的选择。
 */
export function usePaginationParams() {
  const [params, setParams] = useSearchParams();
  const rawSize = params.get("size");
  const size = normalizeSize(rawSize === null ? null : Number(rawSize));
  const parsedOffset = Number(params.get("offset"));
  const offset =
    Number.isFinite(parsedOffset) && parsedOffset > 0 ? parsedOffset : 0;

  const setPageOffset = (next: number) => {
    const target = new URLSearchParams(params);
    target.set("offset", String(Math.max(0, next)));
    setParams(target);
  };

  /** 总页数依赖 total，因此由调用方传进来；换每页条数后停在等价的那一页。 */
  const setSize = (next: number, total: number) => {
    const target = new URLSearchParams(params);
    target.set("size", String(next));
    target.set("offset", String(sizeChangeOffset(offset, size, next, total)));
    setParams(target);
  };

  return { params, setParams, size, offset, setPageOffset, setSize };
}

/** 改筛选条件时保留每页条数：size 是显示偏好，不该被「清空筛选」重置。 */
export function sizeParams(size: number): Record<string, string> {
  return size === DEFAULT_PAGE_SIZE ? {} : { size: String(size) };
}
