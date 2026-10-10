import { Link } from "react-router-dom";
import { useState } from "react";
import { DataTable } from "../../components/DataTable";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
} from "../../components/ui";
import { Pagination } from "../../components/Pagination";
import { showToast } from "../../components/Toast";
import { sizeParams } from "../../lib/usePaginationParams";
import { useSourceActions } from "./useSourceActions";
import { useSourcesList } from "./useSourcesList";
import { sourceColumns } from "./sourceColumns";
import { CategoryDialog } from "./CategoryDialog";
import { SourceDeleteDialog, type DeleteTarget } from "./SourceDeleteDialog";

/** 来源列表页面：行内启停/测试/采集/删除，反馈走顶部浮层提示。 */
export function SourcesPage() {
  const {
    params,
    setParams,
    q,
    category,
    status,
    size,
    offset,
    setPageOffset,
    setSize,
    sources,
    categories,
    collect,
    setFilter,
  } = useSourcesList();
  const actions = useSourceActions();
  const [deleting, setDeleting] = useState<DeleteTarget | null>(null);
  const categoryNames = new Map(
    (categories.data ?? []).map((item) => [item.id, item.name]),
  );
  const columns = sourceColumns(
    categoryNames,
    "/sources?" + params.toString(),
    {
      ...actions,
      requestDelete: (source) =>
        setDeleting({ id: source.id, name: source.name }),
    },
  );
  const items = sources.data?.items ?? [];
  const total = sources.data?.total ?? 0;
  const collectAll = () =>
    collect.mutate(undefined, {
      onSuccess: (data) =>
        showToast({
          type: "info",
          content: (
            <>
              已创建 {data.task_ids.length} 个采集任务，
              <Link to="/tasks">查看任务</Link>。
            </>
          ),
        }),
      onError: (error) => showToast({ type: "error", content: error.message }),
    });
  return (
    <div className="page page-wide">
      <PageHeader
        title="RSS 来源"
        description="来源管理、分类与健康检查。"
        action={
          <>
            <Button
              className="secondary"
              disabled={collect.isPending}
              onClick={collectAll}
            >
              采集全部启用来源
            </Button>
            <Link className="button" to="/sources/new">
              新增来源
            </Link>
          </>
        }
      />
      <Card>
        <div className="toolbar card-pad">
          <Input
            aria-label="搜索来源"
            placeholder="搜索名称、地址或平台"
            value={q}
            onChange={(e) => setFilter("q", e.target.value)}
          />
          <div className="control-group">
            <select
              aria-label="行业分类"
              value={category}
              onChange={(e) => setFilter("category", e.target.value)}
            >
              <option value="">全部行业</option>
              {categories.data?.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <CategoryDialog onSelect={(id) => setFilter("category", id)} />
          </div>
          <select
            aria-label="启用状态"
            value={status}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            <option value="">全部状态</option>
            <option value="true">已启用</option>
            <option value="false">已停用</option>
          </select>
          <Button
            className="secondary"
            onClick={() => setParams(sizeParams(size))}
          >
            清空筛选
          </Button>
          <Button className="secondary" onClick={() => void sources.refetch()}>
            刷新
          </Button>
        </div>
        {sources.isLoading && <div className="loading">正在加载来源…</div>}
        {sources.isError && (
          <ErrorState
            message={sources.error.message}
            onRetry={() => void sources.refetch()}
          />
        )}
        {categories.isError && (
          <ErrorState
            message={"分类加载失败：" + categories.error.message}
            onRetry={() => void categories.refetch()}
          />
        )}
        {total === 0 && (
          <EmptyState
            title={q || category || status ? "没有匹配结果" : "还没有 RSS 来源"}
            description="添加来源或调整筛选条件。"
          />
        )}
        {total > 0 && items.length === 0 && (
          // 有来源但这一页为空：通常是删除后停在旧偏移上。
          <EmptyState
            title="这一页没有内容"
            description={`当前共 ${total} 个来源，这个偏移已经越过末尾。`}
            action={
              <Button className="secondary" onClick={() => setPageOffset(0)}>
                回到第一页
              </Button>
            }
          />
        )}
        {!!items.length && <DataTable data={items} columns={columns} />}
        {total > 0 && (
          <Pagination
            total={total}
            offset={offset}
            size={size}
            disabled={sources.isPlaceholderData}
            onPage={setPageOffset}
            onSize={(next) => setSize(next, total)}
          />
        )}
      </Card>
      <SourceDeleteDialog source={deleting} onClose={() => setDeleting(null)} />
    </div>
  );
}
