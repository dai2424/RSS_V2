import { useState } from "react";
import { Badge, Button, Card, ErrorState } from "../../components/ui";
import { showToast } from "../../components/Toast";
import { formatTime } from "../../lib/display";
import { PromptEditor } from "./PromptEditor";
import { PromptTestPanel } from "./PromptTestPanel";
import { usePromptActions, type Prompt } from "./usePrompts";

/** 右栏详情：只读展示当前版本、启停操作、新建版本与试跑。 */
export function PromptDetail({ prompt }: { prompt: Prompt }) {
  const [editing, setEditing] = useState(false);
  const { activate, archive } = usePromptActions(prompt.task_kind);
  const busy = activate.isPending || archive.isPending;
  const run = (action: "activate" | "archive") => {
    const mutation = action === "activate" ? activate : archive;
    mutation.mutate(prompt.id, {
      onSuccess: () => showToast({ type: "info", content: "已更新提示词状态" }),
      onError: (error: Error) =>
        showToast({ type: "error", content: error.message }),
    });
  };
  return (
    <div className="stack">
      <Card className="card-pad">
        <div className="version-meta">
          <h2>{prompt.version_string}</h2>
          <Badge
            tone={
              prompt.status === "active"
                ? "success"
                : prompt.status === "draft"
                  ? "neutral"
                  : "warning"
            }
          >
            {prompt.status === "active"
              ? "启用中"
              : prompt.status === "draft"
                ? "草稿"
                : "已归档"}
          </Badge>
          <span className="muted">
            {prompt.name} · 更新于 {formatTime(prompt.updated_at)}
          </span>
        </div>
        {prompt.note && <p className="muted">版本说明：{prompt.note}</p>}
        <div className="form-actions">
          <Button
            disabled={busy || prompt.status === "active"}
            onClick={() => run("activate")}
          >
            {prompt.status === "active" ? "当前启用版本" : "启用此版本"}
          </Button>
          <Button
            className="secondary"
            disabled={busy || prompt.status === "archived"}
            onClick={() => run("archive")}
          >
            归档
          </Button>
          <Button
            className="secondary"
            onClick={() => setEditing((value) => !value)}
          >
            {editing ? "取消编辑" : "基于此版本新建"}
          </Button>
        </div>
        {(activate.isError || archive.isError) && (
          <ErrorState message={(activate.error ?? archive.error)!.message} />
        )}
        <div className="prompt-preview">
          <h3>系统提示</h3>
          <pre className="prompt-text">
            {prompt.system_template || "（空）"}
          </pre>
          <h3>用户提示</h3>
          <pre className="prompt-text">{prompt.user_template}</pre>
        </div>
      </Card>
      {editing && (
        <PromptEditor
          taskKind={prompt.task_kind}
          promptKey={prompt.prompt_key}
          base={prompt}
          onDone={() => setEditing(false)}
        />
      )}
      <PromptTestPanel prompt={prompt} />
    </div>
  );
}
