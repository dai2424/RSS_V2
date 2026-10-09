import { useState } from "react";
import { Button, Card, ErrorState } from "../../components/ui";
import { showToast } from "../../components/Toast";
import { usePromptActions, useTaskSpecs, type Prompt } from "./usePrompts";

/** 新建版本：先编译校验与预览，通过后才允许保存。 */
export function PromptEditor({
  taskKind,
  promptKey,
  base,
  onDone,
}: {
  taskKind: string;
  promptKey: string;
  base: Prompt;
  onDone: () => void;
}) {
  const [name, setName] = useState(`${base.name} 的新版本`);
  const [system, setSystem] = useState(base.system_template);
  const [user, setUser] = useState(base.user_template);
  const [note, setNote] = useState("");
  const [compileError, setCompileError] = useState("");
  const { create, compile } = usePromptActions(taskKind);
  const specs = useTaskSpecs();
  const spec = specs.data?.find((item) => item.task_kind === taskKind);
  const result = compile.data;
  const save = () => {
    create.mutate(
      {
        task_kind: taskKind,
        prompt_key: promptKey,
        name,
        system_template: system,
        user_template: user,
        note,
      },
      {
        onSuccess: (prompt) => {
          showToast({
            type: "info",
            content: `已创建 ${prompt.version_string}`,
          });
          onDone();
        },
        onError: (error: Error) => setCompileError(error.message),
      },
    );
  };
  return (
    <Card className="card-pad">
      <h2>新建版本</h2>
      <p className="muted">
        同一提示词标识的版本号自动递增。可用占位符：
        {(spec?.variables ?? []).map((item) => `{{${item}}}`).join("、") ||
          "加载中…"}
        ；必须声明输出字段：{(spec?.output_fields ?? []).join("、")}
      </p>
      <div className="form-field">
        <label htmlFor="prompt-name">版本名称</label>
        <input
          id="prompt-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="prompt-system">系统提示</label>
        <textarea
          id="prompt-system"
          rows={4}
          value={system}
          onChange={(e) => setSystem(e.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="prompt-user">用户提示</label>
        <textarea
          id="prompt-user"
          rows={10}
          value={user}
          onChange={(e) => setUser(e.target.value)}
        />
      </div>
      <div className="form-field">
        <label htmlFor="prompt-note">版本说明</label>
        <input
          id="prompt-note"
          value={note}
          placeholder="这次改了什么"
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      {compileError && (
        <p className="field-error" role="alert">
          {compileError}
        </p>
      )}
      {compile.isError && <ErrorState message={compile.error.message} />}
      {result && (
        <div
          className={result.ok ? "alert" : "alert alert-error"}
          role="status"
        >
          {result.ok ? "编译通过" : "编译未通过"}
          <ul className="issue-list">
            {result.errors.map((item) => (
              <li key={`e-${item.field}-${item.message}`}>
                错误：{item.message}
              </li>
            ))}
            {result.warnings.map((item) => (
              <li key={`w-${item.field}-${item.message}`}>
                提示：{item.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result?.ok && (
        <div className="prompt-preview">
          <h3>渲染预览（系统提示）</h3>
          <pre className="prompt-text">{result.system || "（空）"}</pre>
          <h3>渲染预览（用户提示）</h3>
          <pre className="prompt-text">{result.user}</pre>
        </div>
      )}
      <div className="form-actions">
        <Button
          className="secondary"
          disabled={compile.isPending}
          onClick={() => {
            setCompileError("");
            compile.mutate({
              task_kind: taskKind,
              system_template: system,
              user_template: user,
            });
          }}
        >
          {compile.isPending ? "编译中…" : "编译预览"}
        </Button>
        <Button disabled={create.isPending} onClick={save}>
          {create.isPending ? "保存中…" : "保存为新版本"}
        </Button>
      </div>
    </Card>
  );
}
