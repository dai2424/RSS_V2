import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import type { components } from "../../api/generated";
import { Badge, Button, Switch } from "../../components/ui";
import { domainOf, formatTime } from "../../lib/display";

type Source = components["schemas"]["SourceResponse"];

/** 行内操作回调；pendingId 禁用正在执行的行，避免重复提交。 */
export interface SourceActions {
  pendingId: string;
  toggle: (source: Source) => void;
  test: (source: Source) => void;
  collect: (source: Source) => void;
  requestDelete: (source: Source) => void;
}

/** 集中定义来源表格字段：域名代替完整地址，状态就地切换，健康失败附错误摘要。 */
export function sourceColumns(
  categoryNames: Map<string, string>,
  returnUrl: string,
  actions: SourceActions,
): ColumnDef<Source>[] {
  return [
    {
      header: "来源",
      cell: ({ row }) => (
        <>
          <Link
            className="cell-title"
            onClick={() => sessionStorage.setItem("sources-return", returnUrl)}
            to={"/sources/" + row.original.id}
          >
            {row.original.name}
          </Link>
          <div className="cell-subtitle" title={row.original.url}>
            {domainOf(row.original.url)}
          </div>
        </>
      ),
    },
    {
      header: "行业",
      cell: ({ row }) => categoryNames.get(row.original.category_id) ?? "未知",
    },
    {
      header: "状态",
      cell: ({ row }) => (
        <span className="status-cell">
          <Switch
            checked={row.original.enabled}
            label={row.original.name + " 启停"}
            disabled={actions.pendingId === row.original.id}
            onToggle={() => actions.toggle(row.original)}
          />
          {row.original.enabled ? "已启用" : "已停用"}
        </span>
      ),
    },
    {
      header: "健康",
      cell: ({ row }) => <HealthCell source={row.original} />,
    },
    {
      header: "更新时间",
      cell: ({ row }) => formatTime(row.original.updated_at),
    },
    {
      header: "操作",
      cell: ({ row }) => <ActionCell source={row.original} actions={actions} />,
    },
  ];
}

/** 健康状态；失败时在行内给出错误摘要，完整历史在详情页查看。 */
function HealthCell({ source }: { source: Source }) {
  const health = source.latest_health;
  if (!health) return <span className="muted">未检查</span>;
  return (
    <div className="health-cell">
      <Badge tone={health.parse_success ? "success" : "danger"}>
        {health.parse_success ? "正常" : "失败"}
      </Badge>
      {!health.parse_success && health.error_message && (
        <div className="cell-subtitle error-text" title={health.error_message}>
          {health.error_message}
        </div>
      )}
    </div>
  );
}

/** 行内快捷操作：测试地址与立即采集，处理期间整行按钮禁用。 */
function ActionCell({
  source,
  actions,
}: {
  source: Source;
  actions: SourceActions;
}) {
  const pending = actions.pendingId === source.id;
  return (
    <div className="row-actions">
      <Button
        className="secondary"
        disabled={pending}
        onClick={() => actions.test(source)}
      >
        测试
      </Button>
      <Button
        className="secondary"
        disabled={pending}
        onClick={() => actions.collect(source)}
      >
        采集
      </Button>
      <Button
        className="danger"
        disabled={pending}
        onClick={() => actions.requestDelete(source)}
      >
        删除
      </Button>
    </div>
  );
}
