import { Link } from "react-router-dom";
import type { components } from "../../api/generated";
import { Badge, Switch } from "../../components/ui";
import { formatTime, keywordKindLabels } from "../../lib/display";

type Entry = components["schemas"]["KeywordEntryResponse"];

/** 词表表格：勾选用于合并，常驻开关直接写服务端。 */
export function KeywordTable({
  items,
  selection,
  pinPending,
  onToggle,
  onTogglePin,
}: {
  items: Entry[];
  selection: Set<string>;
  pinPending: boolean;
  onToggle: (item: Entry) => void;
  onTogglePin: (item: Entry) => void;
}) {
  return (
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
            <th scope="col">常驻</th>
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
                <div className="badge-row">
                  <Switch
                    checked={item.pinned}
                    label={`常驻 ${item.text}`}
                    disabled={pinPending}
                    onToggle={() => onTogglePin(item)}
                  />
                  <span className="muted">
                    {item.pinned ? "已常驻" : "非常驻"}
                  </span>
                </div>
              </td>
              <td>
                {/*
                  走结构化关键词检索（keyword=），不是全文搜索：命中由服务端的规范词键与
                  别名解析决定，正文里恰好出现这几个字的无关消息不会被带进来。
                */}
                <Link
                  to={`/messages?keyword=${encodeURIComponent(item.text)}`}
                  onClick={() =>
                    sessionStorage.setItem(
                      "messages-return",
                      `/messages?keyword=${encodeURIComponent(item.text)}`,
                    )
                  }
                >
                  查看消息
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
