import type { components } from "../../api/generated";
import { Badge, Card } from "../../components/ui";
import { formatTime } from "../../lib/display";

type Source = components["schemas"]["SourceResponse"];
type Health = components["schemas"]["HealthResponse"];

/** 原样展示来源配置与 Feed 元数据，不执行 Feed 的 HTML。 */
export function SourceMetadata({ item }: { item: Source }) {
  return (
    <Card className="card-pad">
      <h2>来源与 Feed 元数据</h2>
      <dl className="metadata-list">
        <dt>ID</dt>
        <dd>{item.id}</dd>
        <dt>创建时间</dt>
        <dd>{formatTime(item.created_at)}</dd>
        <dt>更新时间</dt>
        <dd>{formatTime(item.updated_at)}</dd>
        <dt>平台 / 类型</dt>
        <dd>
          {item.platform || "未知"} / {item.source_type}
        </dd>
        <dt>时间偏移</dt>
        <dd>{item.time_offset_minutes} 分钟</dd>
        <dt>标题</dt>
        <dd>{item.feed_title ?? "未知"}</dd>
        <dt>链接</dt>
        <dd>{item.feed_link ?? "未知"}</dd>
        <dt>描述</dt>
        <dd>{item.feed_description ?? "未知"}</dd>
        <dt>语言 / 作者</dt>
        <dd>
          {item.feed_language ?? "未知"} / {item.feed_author ?? "未知"}
        </dd>
        <dt>Feed 更新时间</dt>
        <dd>{formatTime(item.feed_updated_at)}</dd>
      </dl>
      <details>
        <summary>扩展元数据</summary>
        <pre>{JSON.stringify(item.metadata, null, 2)}</pre>
      </details>
    </Card>
  );
}

/** 展示真实健康检查历史，成功时间缺失时保持未知。 */
export function SourceHealth({
  health,
  lastSuccess,
}: {
  health: Health[];
  lastSuccess?: number | null;
}) {
  return (
    <Card className="card-pad">
      <h2>健康记录</h2>
      <p className="muted">最近成功：{formatTime(lastSuccess)}</p>
      {!health.length && <p className="muted">还没有健康记录</p>}
      <div className="stack">
        {health.map((check) => (
          <div className="health-detail" key={check.id}>
            <Badge tone={check.parse_success ? "success" : "danger"}>
              {check.parse_success ? "解析成功" : "解析失败"}
            </Badge>
            <span>{formatTime(check.checked_at)}</span>
            <span>
              HTTP {check.http_status ?? "未知"} · {check.latency_ms ?? "未知"}{" "}
              ms · {check.entry_count} 条
            </span>
            {check.error_message && (
              <p className="alert">
                {check.error_code}：{check.error_message}
              </p>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
