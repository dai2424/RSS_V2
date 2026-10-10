import type { CustomRange } from "../lib/searchRange";
import { withDate } from "../lib/searchRange";

/**
 * 指定日期范围的两个日期输入。
 *
 * 按上海时区的自然日理解（与列表里的时间展示同一口径）：起点当天 0 点、
 * 终点当天 23:59:59。顺序点反时交给 withDate 把另一端拉齐。
 */
export function DateRangeInputs({
  value,
  onChange,
}: {
  value: CustomRange;
  onChange: (next: CustomRange) => void;
}) {
  return (
    <>
      <label htmlFor="range-from">从</label>
      <input
        id="range-from"
        className="input"
        type="date"
        value={value.from}
        onChange={(event) => {
          if (event.target.value)
            onChange(withDate(value, "from", event.target.value));
        }}
      />
      <label htmlFor="range-to">到</label>
      <input
        id="range-to"
        className="input"
        type="date"
        value={value.to}
        onChange={(event) => {
          if (event.target.value)
            onChange(withDate(value, "to", event.target.value));
        }}
      />
    </>
  );
}
