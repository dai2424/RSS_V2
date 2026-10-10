import type { KeyboardEvent } from "react";
import { Button } from "./ui";
import {
  ELLIPSIS,
  PAGE_SIZE_OPTIONS,
  offsetFor,
  pageCountOf,
  pageItems,
  pageOfOffset,
} from "../lib/pagination";

/**
 * 列表分页条：总数、每页条数、页码直选和跳页。
 *
 * 组件只接收当前的 offset / size 并回调新的偏移，状态由调用方保存在 URL 上；
 * 数据在途时用 disabled 挡住连点，避免同一页被反复请求。
 */
export function Pagination({
  total,
  offset,
  size,
  unit = "条",
  disabled = false,
  onPage,
  onSize,
}: {
  total: number; // 当前筛选条件下的记录总数
  offset: number; // 当前页的起始偏移
  size: number; // 每页条数
  unit?: string; // 总数的量词："条" / "个词"
  disabled?: boolean; // 请求在途时禁用翻页
  onPage: (offset: number) => void;
  onSize: (size: number) => void;
}) {
  const pageCount = pageCountOf(total, size);
  const page = Math.min(pageOfOffset(offset, size), pageCount);
  const jump = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    const target = Number(event.currentTarget.value);
    // 跳页输入非法时保持当前页不动，而不是跳到第一页或报错。
    if (!Number.isFinite(target) || target < 1) return;
    onPage(offsetFor(Math.min(Math.floor(target), pageCount), size));
  };
  return (
    <nav className="pagination" aria-label="分页">
      <span className="muted">
        共 {total} {unit}
      </span>
      <div className="pagination-controls">
        <label className="pagination-size">
          每页
          <select
            aria-label="每页条数"
            value={String(size)}
            disabled={disabled}
            onChange={(event) => onSize(Number(event.target.value))}
          >
            {PAGE_SIZE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          条
        </label>
        <Button
          className="secondary"
          disabled={disabled || page <= 1}
          onClick={() => onPage(offsetFor(page - 1, size))}
        >
          上一页
        </Button>
        <div className="pagination-pages">
          {pageItems(page, pageCount).map((item, index) =>
            item === ELLIPSIS ? (
              <span
                key={`gap-${index}`}
                className="pagination-ellipsis"
                aria-hidden="true"
              >
                …
              </span>
            ) : item === page ? (
              // 当前页不可点：用带 aria-current 的记号表示，而不是一个点了没反应的按钮。
              <span
                key={item}
                className="pagination-current"
                aria-current="page"
              >
                {item}
              </span>
            ) : (
              <Button
                key={item}
                className="secondary"
                aria-label={`第 ${item} 页`}
                disabled={disabled}
                onClick={() => onPage(offsetFor(item, size))}
              >
                {item}
              </Button>
            ),
          )}
        </div>
        <Button
          className="secondary"
          disabled={disabled || page >= pageCount}
          onClick={() => onPage(offsetFor(page + 1, size))}
        >
          下一页
        </Button>
        <label className="pagination-jump">
          前往
          <input
            className="input"
            type="number"
            min={1}
            max={pageCount}
            inputMode="numeric"
            aria-label="跳至页码"
            disabled={disabled}
            onKeyDown={jump}
          />
          页
        </label>
      </div>
    </nav>
  );
}
