import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import { showToast } from "../../components/Toast";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { formatTime } from "../../lib/display";

/** 线索：人工选定的一组消息，成员锁定在加入时的版本上。 */
export function LeadsPage() {
  const queryClient = useQueryClient();
  const leads = useQuery({
    queryKey: ["leads"],
    refetchInterval: 10000,
    queryFn: async () => {
      const r = await api.GET("/api/leads", {
        params: { query: { limit: 100 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const remove = useMutation({
    mutationFn: async (leadId: string) => {
      const r = await api.DELETE("/api/leads/{lead_id}", {
        params: { path: { lead_id: leadId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      showToast({
        type: "success",
        content: "已删除这条线索，消息本身不受影响。",
      });
      void queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  const items = leads.data?.items ?? [];
  return (
    <div className="page page-wide">
      <PageHeader
        title="线索"
        description="把同一条线索的消息放在一起，供生成草稿使用；成员锁定在加入时的版本上。"
      />
      {leads.isLoading && <div className="loading">正在加载线索…</div>}
      {leads.isError && (
        <ErrorState
          message={leads.error.message}
          onRetry={() => void leads.refetch()}
        />
      )}
      {leads.data && items.length === 0 && (
        <EmptyState
          title="还没有线索"
          description="到消息列表勾选两条以上同一件事的消息，用「加入线索」建一条。"
          action={
            <Link className="button secondary" to="/messages">
              去消息列表
            </Link>
          }
        />
      )}
      {items.length > 0 && (
        <Card>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">线索</th>
                  <th scope="col">成员</th>
                  <th scope="col">最近变化</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((lead) => (
                  <tr key={lead.id}>
                    <td>
                      <Link className="cell-title" to={`/leads/${lead.id}`}>
                        {lead.title}
                      </Link>
                      {lead.note && (
                        <div className="cell-subtitle">{lead.note}</div>
                      )}
                    </td>
                    <td>{lead.members}</td>
                    <td>{formatTime(lead.updated_at)}</td>
                    <td>
                      <div className="toolbar">
                        <Link
                          className="button secondary"
                          to={`/leads/${lead.id}`}
                        >
                          打开
                        </Link>
                        <Button
                          className="danger"
                          disabled={remove.isPending}
                          onClick={() => remove.mutate(lead.id)}
                        >
                          删除
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/** 线索详情：成员按时间从早到晚，移除成员不影响消息本身。 */
export function LeadDetailPage() {
  const { leadId = "" } = useParams();
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["lead", leadId],
    queryFn: async () => {
      const r = await api.GET("/api/leads/{lead_id}", {
        params: { path: { lead_id: leadId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const removeMember = useMutation({
    mutationFn: async (versionId: string) => {
      const r = await api.DELETE("/api/leads/{lead_id}/messages/{version_id}", {
        params: { path: { lead_id: leadId, version_id: versionId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      showToast({ type: "success", content: "已从线索里移除这条消息。" });
      void queryClient.invalidateQueries({ queryKey: ["lead", leadId] });
      void queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  if (detail.isLoading) return <div className="loading">正在加载线索…</div>;
  if (detail.isError || !detail.data) {
    return (
      <ErrorState
        message={detail.error?.message ?? "线索不存在"}
        onRetry={() => void detail.refetch()}
      />
    );
  }
  const lead = detail.data;
  return (
    <div className="page page-reading">
      <PageHeader
        title={lead.title}
        description={
          lead.note ?? "成员按发布时间从早到晚；引用锁定在加入时的版本。"
        }
        action={
          <Link className="button secondary" to="/leads">
            返回线索
          </Link>
        }
      />
      <Card className="card-pad">
        <div className="toolbar">
          <h2>成员（{lead.members.length}）</h2>
          <Link
            className="button secondary"
            to="/messages"
            title="到消息列表勾选后加入这条线索"
          >
            添加消息
          </Link>
        </div>
        {lead.members.length < 2 && (
          <div className="alert">
            线索至少要有两条消息才能生成草稿；当前
            {lead.members.length === 0
              ? "没有成员（消息可能已被删除）"
              : "只有一条成员"}
            。
          </div>
        )}
        <ol className="related-list">
          {lead.members.map((member) => (
            <li key={member.version_id} className="related-item">
              <div className="related-item-shared">
                <Badge>v{member.version_number}</Badge>
                <span className="muted">
                  {member.source_name} ·{" "}
                  {formatTime(member.published_at ?? member.collected_at)}
                </span>
              </div>
              <Link
                className="cell-title"
                to={`/messages/${member.message_id}`}
              >
                {member.title || "无标题"}
              </Link>
              <div className="toolbar">
                <Button
                  className="secondary"
                  disabled={removeMember.isPending}
                  onClick={() => removeMember.mutate(member.version_id)}
                >
                  从线索移除
                </Button>
              </div>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}
