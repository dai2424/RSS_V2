import { useEffect, useState } from "react";
import { Button, Card, ErrorState } from "../../components/ui";
import { showToast } from "../../components/Toast";
import { usePrompts } from "../prompts/usePrompts";
import { useTaskSettings, type TaskSetting } from "./useTaskSettings";

/** 一行的编辑态：空字符串表示继承上一层。 */
type Row = { enabled: string; promptId: string };

function toRows(settings: TaskSetting[]): Record<string, Row> {
  return Object.fromEntries(
    settings.map((item) => [
      item.task_kind,
      {
        enabled: item.enabled === null ? "" : item.enabled ? "on" : "off",
        promptId: item.prompt_id ?? "",
      },
    ]),
  );
}

/**
 * 任务分配表：来源与分类共用。每类任务一行，可分别设置开关与提示词；
 * 留空表示继承上一层，生效值在右侧显示。
 */
export function TaskSettingTable({
  scope,
  scopeId,
  title,
}: {
  scope: "source" | "category";
  scopeId: string;
  title: string;
}) {
  const { query, save } = useTaskSettings(scope, scopeId);
  const prompts = usePrompts();
  const [rows, setRows] = useState<Record<string, Row>>({});
  useEffect(() => {
    if (query.data) setRows(toRows(query.data));
  }, [query.data]);
  if (query.isLoading) return <div className="loading">正在加载任务配置…</div>;
  if (query.isError) {
    return (
      <ErrorState
        message={query.error.message}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const settings = query.data ?? [];
  const submit = () =>
    save.mutate(
      {
        settings: settings.map((item) => ({
          task_kind: item.task_kind,
          enabled:
            rows[item.task_kind]?.enabled === ""
              ? null
              : rows[item.task_kind]?.enabled === "on",
          prompt_id: rows[item.task_kind]?.promptId || null,
        })),
      },
      {
        onSuccess: () => showToast({ type: "info", content: "已保存任务配置" }),
        onError: (error: Error) =>
          showToast({ type: "error", content: error.message }),
      },
    );
  const update = (kind: string, patch: Partial<Row>) =>
    setRows({ ...rows, [kind]: { ...rows[kind], ...patch } });
  return (
    <Card className="card-pad">
      <h2>{title}</h2>
      <p className="muted">
        留空表示继承上一层：来源继承行业分类，行业分类继承全局默认。
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>自动执行</th>
              <th>提示词</th>
              <th>生效结果</th>
            </tr>
          </thead>
          <tbody>
            {settings.map((item) => (
              <tr key={item.task_kind}>
                <td>{item.label}</td>
                <td>
                  <select
                    aria-label={`${item.label} 自动执行`}
                    value={rows[item.task_kind]?.enabled ?? ""}
                    onChange={(e) =>
                      update(item.task_kind, { enabled: e.target.value })
                    }
                  >
                    <option value="">继承</option>
                    <option value="on">启用</option>
                    <option value="off">停用</option>
                  </select>
                </td>
                <td>
                  <select
                    aria-label={`${item.label} 提示词`}
                    value={rows[item.task_kind]?.promptId ?? ""}
                    onChange={(e) =>
                      update(item.task_kind, { promptId: e.target.value })
                    }
                  >
                    <option value="">继承</option>
                    {(prompts.data ?? [])
                      .filter(
                        (prompt) =>
                          prompt.task_kind === item.task_kind &&
                          prompt.status !== "archived",
                      )
                      .map((prompt) => (
                        <option key={prompt.id} value={prompt.id}>
                          {prompt.version_string}
                        </option>
                      ))}
                  </select>
                </td>
                <td className="muted">
                  {item.effective_enabled ? "执行" : "不执行"} ·{" "}
                  {item.effective_prompt_version ?? "无提示词"}（
                  {item.scope === "source"
                    ? "来自来源"
                    : item.scope === "category"
                      ? "来自分类"
                      : "全局默认"}
                  ）
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form-actions">
        <Button disabled={save.isPending} onClick={submit}>
          {save.isPending ? "保存中…" : "保存任务配置"}
        </Button>
      </div>
      {save.isError && <ErrorState message={save.error.message} />}
    </Card>
  );
}
