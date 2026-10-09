import { useState } from "react";
import { Badge, Button, Card, ErrorState } from "../../components/ui";
import { showToast } from "../../components/Toast";
import { formatTime } from "../../lib/display";
import { PromptDeleteDialog } from "./PromptDeleteDialog";
import { PromptEditor } from "./PromptEditor";
import { PromptTestPanel } from "./PromptTestPanel";
import {
  usePromptActions,
  usePromptUsage,
  type Prompt,
  type PromptUsage,
} from "./usePrompts";

/** 使用情况说明：用服务端计数解释为什么某个按钮被禁用，不靠颜色或图标表达。 */
function usageText(usage: PromptUsage | undefined, prompt: Prompt): string {
  if (!usage) return "正在检查使用情况…";
  if (usage.used) {
    const parts = [
      usage.calls ? `${usage.calls} 次模型调用` : "",
      usage.results ? `${usage.results} 条机器生成结果` : "",
      usage.tasks ? `${usage.tasks} 个任务` : "",
      usage.bindings ? `${usage.bindings} 处来源或分类分配` : "",
    ].filter(Boolean);
    return `该版本已被 ${parts.join("、")} 引用，改动会让这些记录失去指向，只能另存为新版本或归档。`;
  }
  if (prompt.status === "active") {
    return "该版本还没有被任何调用、结果、任务或分配引用，可以直接编辑；它正在启用，删除前请先启用另一个版本。";
  }
  if (prompt.status === "draft") {
    return "该版本还没有被任何调用、结果、任务或分配引用，可以就地编辑或删除。";
  }
  return "该版本没有被引用，可以删除；已归档的版本不再就地编辑，需要改文本请另存为新版本。";
}

/** 右栏详情：只读展示当前版本、启停与编辑删除操作、新建版本与试跑。 */
export function PromptDetail({ prompt }: { prompt: Prompt }) {
  const [mode, setMode] = useState<"edit" | "version" | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { activate, archive } = usePromptActions(prompt.task_kind);
  const usage = usePromptUsage(prompt.id);
  const active = prompt.status === "active";
  const used = usage.data?.used ?? false;
  const busy = activate.isPending || archive.isPending;
  // 就地编辑只对草稿开放：改启用中的版本等于顺带改了所有继承它的来源。
  const editable =
    prompt.status === "draft" && usage.data !== undefined && !used;
  const removable = usage.data !== undefined && !used && !active;
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
              active
                ? "success"
                : prompt.status === "draft"
                  ? "neutral"
                  : "warning"
            }
          >
            {active ? "启用中" : prompt.status === "draft" ? "草稿" : "已归档"}
          </Badge>
          <span className="muted">
            {prompt.name} · 更新于 {formatTime(prompt.updated_at)}
          </span>
        </div>
        {prompt.note && <p className="muted">版本说明：{prompt.note}</p>}
        <div className="form-actions">
          <Button disabled={busy || active} onClick={() => run("activate")}>
            {active ? "当前启用版本" : "启用此版本"}
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
            disabled={!editable}
            onClick={() => setMode("edit")}
          >
            编辑
          </Button>
          <Button className="secondary" onClick={() => setMode("version")}>
            另存为新版本
          </Button>
          <Button
            className="danger-outline"
            disabled={!removable}
            onClick={() => setDeleting(true)}
          >
            删除
          </Button>
        </div>
        <p className="muted">{usageText(usage.data, prompt)}</p>
        {usage.isError && (
          <ErrorState
            message={usage.error.message}
            onRetry={() => void usage.refetch()}
          />
        )}
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
      {mode && (
        <PromptEditor mode={mode} base={prompt} onDone={() => setMode(null)} />
      )}
      <PromptTestPanel prompt={prompt} />
      <PromptDeleteDialog
        prompt={deleting ? prompt : null}
        onClose={() => setDeleting(false)}
        onDeleted={() => void usage.refetch()}
      />
    </div>
  );
}
