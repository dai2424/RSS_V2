import { Link } from "react-router-dom";
import type { components } from "../../api/generated";
import { Badge, Card, EmptyState, ErrorState } from "../../components/ui";
import { Pagination } from "../../components/Pagination";
import { formatTime, keywordKindLabels } from "../../lib/display";

type Entry = components["schemas"]["KeywordEntryResponse"];

/** 词表：按出现次数排序的规范词；勾选若干行后可以合并。 */
export function KeywordVocabulary({
  items,
  total,
  totalTerms,
  q,
  kind,
  minCount,
  offset,
  size,
  loading,
  paging,
  error,
  onRetry,
  onFilter,
  onPage,
  onSize,
  onClear,
  selection,
  onToggle,
}: {
  items: Entry[];
  total: number;
  totalTerms: number;
  q: string;
  kind: string;
  minCount: number;
  offset: number;
  size: number;
  loading: boolean;
  paging: boolean;
  error?: string;
  onRetry: () => void;
  onFilter: (key: string, value: string) => void;
  onPage: (offset: number) => void;
  onSize: (size: number) => void;
  onClear: () => void;
  selection: Set<string>;
  onToggle: (item: Entry) => void;
}) {
  const filtered = Boolean(q || kind || minCount !== 2);
  return (
    <Card className="card-pad">
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
            onFilter(
              "min",
              event.target.value === "2" ? "" : event.target.value,
            )
          }
        >
          <option value="2">2 次以上（折叠孤词）</option>
          <option value="1">1 次以上（含孤词）</option>
          <option value="3">3 次以上</option>
          <option value="5">5 次以上</option>
        </select>
        {filtered && (
          <button type="button" className="button secondary" onClick={onClear}>
            清空筛选
          </button>
        )}
      </div>
      {loading && <div className="loading">正在加载词表…</div>}
      {error && <ErrorState message={error} onRetry={onRetry} />}
      {!loading && !error && items.length === 0 && (
        <EmptyState
          title={
            total > 0 ? "这一页没有内容" : emptyTitle(filtered, totalTerms)
          }
          description={
            total > 0
              ? `当前共 ${total} 个词，这个偏移已经越过末尾。`
              : emptyHint(filtered, totalTerms)
          }
          action={
            total > 0 ? (
              <button
                type="button"
                className="button secondary"
                onClick={() => onPage(0)}
              >
                回到第一页
              </button>
            ) : undefined
          }
        />
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="keyword-table">
              <thead>
                <tr>
                  <th scope="col">选择</th>
                  <th scope="col">关键词</th>
                  <th scope="col">类型</th>
                  <th scope="col">出现次数</th>
                  <th scope="col">来源数</th>
                  <th scope="col">首次出现</th>
                  <th scope="col">最近出现</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.key}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selection.has(item.key)}
                        aria-label={`选择 ${item.text}`}
                        onChange={() => onToggle(item)}
                      />
                    </td>
                    <td>
                      <span className="cell-title">{item.text}</span>
                      {item.aliases.length > 0 && (
                        <span className="cell-subtitle">
                          已并入：{item.aliases.join("、")}
                        </span>
                      )}
                    </td>
                    <td>
                      <Badge
                        tone="neutral"
                        title={keywordKindLabels[item.kind] ?? item.kind}
                      >
                        {item.kind}
                      </Badge>
                    </td>
                    <td>{item.mentions}</td>
                    <td>{item.sources}</td>
                    <td>{formatTime(item.first_seen_at)}</td>
                    <td>{formatTime(item.last_seen_at)}</td>
                    <td>
                      <Link to={`/messages?q=${encodeURIComponent(item.text)}`}>
                        查看消息
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            total={total}
            offset={offset}
            size={size}
            unit="个词"
            disabled={paging}
            onPage={onPage}
            onSize={onSize}
          />
        </>
      )}
    </Card>
  );
}

/** 空态要区分"没有词"和"词都被折叠了"，否则用户会以为数据没进来。 */
function emptyTitle(filtered: boolean, totalTerms: number): string {
  if (filtered) return "没有匹配的关键词";
  if (totalTerms > 0) return "词表里只有出现一次的写法";
  return "还没有关键词";
}

function emptyHint(filtered: boolean, totalTerms: number): string {
  if (filtered) return "换一个写法或放宽筛选条件试试。";
  if (totalTerms > 0)
    return `共有 ${totalTerms} 个词只出现过一次，默认不展开；把最小出现次数改成包含孤词即可查看。`;
  return "关键词由内容加工产出，可以在页面右上角回填存量消息。";
}
