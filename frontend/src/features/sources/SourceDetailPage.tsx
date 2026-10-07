import { Link } from "react-router-dom";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { showToast } from "../../components/Toast";
import { formatTime, statusLabels } from "../../lib/display";
import { useSourceDetail } from "./useSourceDetail";
import { SourceMetadata, SourceHealth } from "./SourceHistory";

/** 来源详情和操作入口；历史展示与数据用例分别维护。 */
export function SourceDetailPage() {
  const { source, testSource, toggleSource, collect } = useSourceDetail();
  if (source.isPending)
    return (
      <div className="loading" role="status">
        正在加载来源…
      </div>
    );
  if (source.isError)
    return (
      <ErrorState
        message={source.error.message}
        onRetry={() => void source.refetch()}
      />
    );
  const { source: item, health, latest_collection: run } = source.data;
  const busy =
    toggleSource.isPending || testSource.isPending || collect.isPending;
  const reportError = (error: Error) =>
    showToast({ type: "error", content: error.message });
  return (
    <div className="page">
      <PageHeader
        title={item.name}
        description={item.url}
        action={
          <>
            <Link
              className="button secondary"
              to={sessionStorage.getItem("sources-return") || "/sources"}
            >
              返回列表
            </Link>
            <Link className="button secondary" to={`/sources/${item.id}/edit`}>
              编辑
            </Link>
          </>
        }
      />
      <div className="detail-grid">
        <div className="stack">
          <Card className="card-pad">
            <div className="split">
              <strong>来源状态</strong>
              <Badge tone={item.enabled ? "success" : "neutral"}>
                {item.enabled ? "已启用" : "已停用"}
              </Badge>
            </div>
            <p className="muted">停用只影响后续采集，历史消息保留。</p>
            <div className="toolbar">
              <Button
                className="secondary"
                onClick={() =>
                  toggleSource.mutate(!item.enabled, {
                    onError: reportError,
                  })
                }
                disabled={busy}
              >
                {item.enabled ? "停用来源" : "启用来源"}
              </Button>
              <Button
                className="secondary"
                onClick={() =>
                  testSource.mutate(undefined, { onError: reportError })
                }
                disabled={busy}
              >
                {testSource.isPending ? "检查中…" : "测试来源"}
              </Button>
              <Button
                onClick={() =>
                  collect.mutate(undefined, {
                    onSuccess: () =>
                      showToast({
                        type: "info",
                        content: (
                          <>
                            已创建采集任务，<Link to="/tasks">查看任务</Link>。
                          </>
                        ),
                      }),
                    onError: reportError,
                  })
                }
                disabled={busy || !item.enabled}
              >
                {collect.isPending ? "创建任务…" : "立即采集"}
              </Button>
            </div>
          </Card>
          <Card className="card-pad">
            <h2>最近采集</h2>
            {run ? (
              <>
                <Badge>{statusLabels[run.status]}</Badge>
                <p className="muted">{formatTime(run.requested_at)}</p>
                <p>
                  新增 {run.created_count} · 更新 {run.updated_count} · 跳过{" "}
                  {run.skipped_count} · 失败 {run.failed_count}
                </p>
                <Link className="text-link" to={`/messages?source=${item.id}`}>
                  查看本来源消息
                </Link>
              </>
            ) : (
              <p className="muted">尚未采集</p>
            )}
          </Card>
          <SourceMetadata item={item} />
        </div>
        <SourceHealth health={health} lastSuccess={item.last_success_at} />
      </div>
    </div>
  );
}
