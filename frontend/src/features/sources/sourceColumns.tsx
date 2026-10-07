import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import type { components } from "../../api/generated";
import { Badge } from "../../components/ui";
import { formatTime, statusLabels } from "../../lib/display";

type Source = components["schemas"]["SourceResponse"];

/** 集中定义来源表格字段和真实健康状态，链接记住列表上下文。 */
export function sourceColumns(
  categoryNames: Map<string, string>,
  returnUrl: string,
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
          <div className="cell-subtitle">{row.original.url}</div>
        </>
      ),
    },
    {
      header: "行业",
      cell: ({ row }) => categoryNames.get(row.original.category_id) ?? "未知",
    },
    { header: "语言", cell: ({ row }) => statusLabels[row.original.language] },
    {
      header: "状态",
      cell: ({ row }) => (
        <Badge tone={row.original.enabled ? "success" : "neutral"}>
          {row.original.enabled ? "已启用" : "已停用"}
        </Badge>
      ),
    },
    {
      header: "健康",
      cell: ({ row }) =>
        row.original.latest_health ? (
          <Badge
            tone={
              row.original.latest_health.parse_success ? "success" : "danger"
            }
          >
            {row.original.latest_health.parse_success ? "正常" : "失败"}
          </Badge>
        ) : (
          <span className="muted">未检查</span>
        ),
    },
    {
      header: "更新时间",
      cell: ({ row }) => formatTime(row.original.updated_at),
    },
  ];
}
