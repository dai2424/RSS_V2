import { Link } from "react-router-dom";
import { DataTable } from "../../components/DataTable";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
} from "../../components/ui";
import { showToast } from "../../components/Toast";
import { useSourceActions } from "./useSourceActions";
import { useSourcesList } from "./useSourcesList";
import { sourceColumns } from "./sourceColumns";
import { CategoryDialog } from "./CategoryDialog";

const PAGE_SIZE = 25;

/** 来源列表页面：行内启停/测试/采集，反馈走顶部浮层提示。 */
export function SourcesPage() {
  const {
    params,
    setParams,
    q,
    category,
    status,
    offset,
    sources,
    categories,
    collect,
    setFilter,
  } = useSourcesList();
  const actions = useSourceActions();
  const categoryNames = new Map(
    (categories.data ?? []).map((item) => [item.id, item.name]),
  );
  const columns = sourceColumns(
    categoryNames,
    "/sources?" + params.toString(),
    actions,
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
    <div className="page">
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
          <Button className="secondary" onClick={() => setParams({})}>
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
        {sources.data?.items.length === 0 && (
          <EmptyState
            title={q || category || status ? "没有匹配结果" : "还没有 RSS 来源"}
            description="添加来源或调整筛选条件。"
          />
        )}
        {!!items.length && <DataTable data={items} columns={columns} />}
        <div className="toolbar card-pad">
          <Button
            className="secondary"
            disabled={offset === 0}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("offset", String(Math.max(0, offset - PAGE_SIZE)));
              setParams(next);
            }}
          >
            上一页
          </Button>
          <span className="muted">
            共 {total} 条 · 第 {offset / PAGE_SIZE + 1} 页
          </span>
          <Button
            className="secondary"
            disabled={offset + items.length >= total}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("offset", String(offset + PAGE_SIZE));
              setParams(next);
            }}
          >
            下一页
          </Button>
        </div>
      </Card>
    </div>
  );
}
