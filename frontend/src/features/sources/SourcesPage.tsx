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
import { useSourcesList } from "./useSourcesList";
import { sourceColumns } from "./sourceColumns";
import { CategoryForm } from "./CategoryForm";

/** 来源列表页面，复用 URL 数据用例、表格字段和分类表单。 */
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
  const categoryNames = new Map(
    (categories.data ?? []).map((item) => [item.id, item.name]),
  );
  const columns = sourceColumns(categoryNames, "/sources?" + params.toString());
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
              onClick={() => collect.mutate()}
            >
              采集全部启用来源
            </Button>
            <Link className="button" to="/sources/new">
              新增来源
            </Link>
          </>
        }
      />
      {collect.data && (
        <div className="alert" role="status">
          已创建 {collect.data.task_ids.length} 个采集任务。
          <Link to="/tasks">查看任务</Link>
        </div>
      )}
      {collect.isError && <ErrorState message={collect.error.message} />}
      <Card>
        <div className="toolbar card-pad">
          <Input
            aria-label="搜索来源"
            placeholder="搜索名称、地址或平台"
            value={q}
            onChange={(e) => setFilter("q", e.target.value)}
          />
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
        {sources.data?.length === 0 && (
          <EmptyState
            title={q || category || status ? "没有匹配结果" : "还没有 RSS 来源"}
            description="添加来源或调整筛选条件。"
          />
        )}
        {!!sources.data?.length && (
          <DataTable data={sources.data} columns={columns} />
        )}
        <div className="toolbar card-pad">
          <Button
            className="secondary"
            disabled={offset === 0}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("offset", String(Math.max(0, offset - 25)));
              setParams(next);
            }}
          >
            上一页
          </Button>
          <span className="muted">第 {offset / 25 + 1} 页</span>
          <Button
            className="secondary"
            disabled={(sources.data?.length ?? 0) < 25}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("offset", String(offset + 25));
              setParams(next);
            }}
          >
            下一页
          </Button>
        </div>
      </Card>
      <CategoryForm />
    </div>
  );
}
