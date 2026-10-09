import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import { Badge, ErrorState } from "../../components/ui";
import { formatTime, keywordKindLabels } from "../../lib/display";

/** 相关消息：共享关键词的其他消息，用来顺着同一条线索继续读。 */
export function RelatedMessages({ messageId }: { messageId: string }) {
  const related = useQuery({
    queryKey: ["related", messageId],
    queryFn: async () => {
      const r = await api.GET("/api/messages/{message_id}/related", {
        params: { path: { message_id: messageId }, query: { limit: 8 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  if (related.isLoading) {
    return <div className="loading">正在查找相关消息…</div>;
  }
  if (related.isError) {
    return (
      <ErrorState
        message={related.error.message}
        onRetry={() => void related.refetch()}
      />
    );
  }
  const rows = related.data ?? [];
  return (
    <section className="related-messages">
      <h2>相关消息</h2>
      {rows.length === 0 ? (
        <p className="muted">
          没有共享关键词的其他消息。关键词由内容加工产出，可在顶部手动生成。
        </p>
      ) : (
        <ul className="related-list">
          {rows.map((item) => (
            <li key={item.message_id}>
              <Link to={`/messages/${item.message_id}`}>{item.title}</Link>
              <span className="muted">
                发布：{formatTime(item.published_at)} · 共享：
              </span>
              <span className="badge-row">
                {item.shared.map((keyword) => (
                  <Badge
                    key={keyword.text}
                    tone="neutral"
                    title={keywordKindLabels[keyword.kind] ?? keyword.kind}
                  >
                    {keyword.text}
                  </Badge>
                ))}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
