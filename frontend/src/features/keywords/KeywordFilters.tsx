/** 词表筛选：搜索、类型、最小出现次数与常驻；筛选状态由父组件写在 URL 上。 */
export function KeywordFilters({
  q,
  kind,
  minCount,
  pinned,
  filtered,
  onFilter,
  onClear,
}: {
  q: string;
  kind: string;
  minCount: number;
  pinned: boolean;
  filtered: boolean;
  onFilter: (key: string, value: string) => void;
  onClear: () => void;
}) {
  return (
    <div className="toolbar">
      <label htmlFor="keyword-search">搜索关键词</label>
      <input
        id="keyword-search"
        className="input"
        value={q}
        placeholder="按词的写法搜索"
        onChange={(event) => onFilter("q", event.target.value)}
      />
      <label htmlFor="keyword-kind">类型</label>
      <select
        id="keyword-kind"
        value={kind}
        onChange={(event) => onFilter("kind", event.target.value)}
      >
        <option value="">全部</option>
        <option value="entity">实体</option>
        <option value="topic">主题</option>
        <option value="event">事件</option>
      </select>
      <label htmlFor="keyword-min">最小出现次数</label>
      <select
        id="keyword-min"
        value={String(minCount)}
        onChange={(event) =>
          onFilter("min", event.target.value === "2" ? "" : event.target.value)
        }
      >
        <option value="2">2 次以上（折叠孤词）</option>
        <option value="1">1 次以上（含孤词）</option>
        <option value="3">3 次以上</option>
        <option value="5">5 次以上</option>
      </select>
      <label htmlFor="keyword-pinned">只看常驻</label>
      <select
        id="keyword-pinned"
        value={pinned ? "1" : ""}
        onChange={(event) => onFilter("pinned", event.target.value)}
      >
        <option value="">全部词</option>
        <option value="1">只看常驻</option>
      </select>
      {filtered && (
        <button type="button" className="button secondary" onClick={onClear}>
          清空筛选
        </button>
      )}
    </div>
  );
}
