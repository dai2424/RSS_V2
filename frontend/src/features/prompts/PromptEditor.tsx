import { useState } from "react";
import { Button, Card, ErrorState } from "../../components/ui";
import { showToast } from "../../components/Toast";
import { PromptCompilePreview } from "./PromptCompilePreview";
import { PromptCreateFields } from "./PromptCreateFields";
import { usePromptActions, useTaskSpecs, type Prompt } from "./usePrompts";

type PromptEditorMode = "create" | "edit" | "version";

/** 新建没有基准版本；就地编辑与另存新版本都以现有版本为模板。 */
type PromptEditorProps = {
  mode: PromptEditorMode;
  base?: Prompt | null;
  onDone: () => void;
  onCreated?: (prompt: Prompt) => void;
};

/** 提交按钮文案：三种模式的差别只有文案与提交目标。 */
const SAVE_LABELS: Record<PromptEditorMode, string> = {
  create: "创建提示词",
  edit: "保存修改",
  version: "保存为新版本",
};

function editorTitle(mode: PromptEditorMode, base: Prompt | null): string {
  if (mode === "create") return "新建提示词";
  if (mode === "edit") return `就地编辑 ${base?.version_string ?? ""}`;
  return `基于 ${base?.version_string ?? ""} 新建版本`;
}

/**
 * 提示词表单：新建、就地编辑、另存新版本共用一套字段与编译预览。
 *
 * - `create`：选任务类型并填全新业务键，因此从空模板开始；
 * - `edit`：改现有版本的文本，版本串不变（只对没被引用过的草稿开放）；
 * - `version`：以现有版本为底稿新建下一个版本号。
 */
export function PromptEditor(props: PromptEditorProps) {
  const { mode, onDone, onCreated } = props;
  const base = props.mode === "create" ? null : (props.base ?? null);
  const [taskKind, setTaskKind] = useState(base?.task_kind ?? "");
  const [promptKey, setPromptKey] = useState(base?.prompt_key ?? "");
  const [name, setName] = useState(
    base && mode === "version" ? `${base.name} 的新版本` : (base?.name ?? ""),
  );
  const [system, setSystem] = useState(base?.system_template ?? "");
  const [user, setUser] = useState(base?.user_template ?? "");
  const [note, setNote] = useState(mode === "edit" ? (base?.note ?? "") : "");
  const [formError, setFormError] = useState("");
  const { create, update, compile } = usePromptActions();
  const specs = useTaskSpecs();
  // 新建时任务类型默认取第一个规格；用派生值而不是副作用，避免首帧出现空选。
  const selectedKind = taskKind || specs.data?.[0]?.task_kind || "";
  const spec = specs.data?.find((item) => item.task_kind === selectedKind);
  const result = compile.data;
  const saving = create.isPending || update.isPending;
  const title = editorTitle(mode, base);

  const done = (prompt: Prompt) => {
    showToast({
      type: "info",
      content:
        mode === "edit"
          ? `已保存 ${prompt.version_string} 的修改`
          : `已创建 ${prompt.version_string}`,
    });
    onCreated?.(prompt);
    onDone();
  };
  const save = () => {
    setFormError("");
    const body = {
      name,
      system_template: system,
      user_template: user,
      note,
    };
    const failed = (error: Error) => setFormError(error.message);
    if (mode === "edit" && base) {
      update.mutate(
        { promptId: base.id, body },
        { onSuccess: done, onError: failed },
      );
      return;
    }
    create.mutate(
      {
        task_kind: selectedKind,
        prompt_key: mode === "create" ? promptKey : (base?.prompt_key ?? ""),
        ...body,
      },
      { onSuccess: done, onError: failed },
    );
  };
  return (
    <Card className="card-pad">
      <h2>{title}</h2>
      <p className="muted">
        可用占位符：
        {(spec?.variables ?? []).map((item) => `{{${item}}}`).join("、") ||
          "加载中…"}
        ；必须声明输出字段：{(spec?.output_fields ?? []).join("、")}
      </p>
      {mode === "create" && (
        <PromptCreateFields
          taskKind={selectedKind}
          promptKey={promptKey}
          options={specs.data ?? []}
          onTaskKindChange={setTaskKind}
          onPromptKeyChange={setPromptKey}
        />
      )}
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
      {formError && (
        <p className="field-error" role="alert">
          {formError}
        </p>
      )}
      {compile.isError && <ErrorState message={compile.error.message} />}
      {result && <PromptCompilePreview result={result} />}
      <div className="form-actions">
        <Button
          className="secondary"
          disabled={compile.isPending}
          onClick={() => {
            setFormError("");
            compile.mutate({
              task_kind: selectedKind,
              system_template: system,
              user_template: user,
            });
          }}
        >
          {compile.isPending ? "编译中…" : "编译预览"}
        </Button>
        <Button disabled={saving} onClick={save}>
          {saving ? "保存中…" : SAVE_LABELS[mode]}
        </Button>
        <Button className="secondary" disabled={saving} onClick={onDone}>
          取消
        </Button>
      </div>
    </Card>
  );
}
