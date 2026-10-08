import { useMutation } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { api, requireResponse } from "../../api/client";
import { showToast } from "../../components/Toast";
import { Button, Input, Switch } from "../../components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";
import { UnsavedDialog } from "../../components/UnsavedChanges";
import { useFormDirty, type DirtyChange, type ProviderModel } from "./types";

/** 单个模型行：测试、编辑与启停；编辑在弹层中修改 ID 与优先级。 */
export function ModelRow({
  item,
  testing,
  onTest,
  onSaved,
  onDirtyChange,
}: {
  item: ProviderModel;
  testing: boolean;
  onTest: () => void;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const [editing, setEditing] = useState(false);
  const toggle = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH(
        "/api/llm/providers/{provider_id}/models/{model_id}",
        {
          params: {
            path: { provider_id: item.provider_id, model_id: item.id },
          },
          body: { enabled: !item.enabled },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: onSaved,
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  return (
    <div className="provider-row">
      <code className="provider-row-title">{item.model}</code>
      <span className="muted">优先级 {item.priority}</span>
      <span className="muted">{item.enabled ? "已启用" : "已停用"}</span>
      <div className="row-actions">
        <Button className="secondary" disabled={testing} onClick={onTest}>
          {testing ? "测试中…" : "测试"}
        </Button>
        <Button className="secondary" onClick={() => setEditing(true)}>
          编辑
        </Button>
        <Switch
          checked={item.enabled}
          label={`启用模型 ${item.model}`}
          disabled={toggle.isPending}
          onToggle={() => toggle.mutate()}
        />
      </div>
      {toggle.isError && (
        <p className="field-error" role="alert">
          {toggle.error.message}
        </p>
      )}
      <ModelEditDialog
        item={item}
        open={editing}
        onOpenChange={setEditing}
        onSaved={onSaved}
        onDirtyChange={onDirtyChange}
      />
    </div>
  );
}

/** 模型 ID 与优先级的编辑弹层；保存失败保留输入，关闭前确认。 */
function ModelEditDialog({
  item,
  open,
  onOpenChange,
  onSaved,
  onDirtyChange,
}: {
  item: ProviderModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const [model, setModel] = useState(item.model);
  const [priority, setPriority] = useState(item.priority);
  const dirty = model !== item.model || priority !== item.priority;
  useFormDirty("model-" + item.id, dirty && open, onDirtyChange);
  const [confirming, setConfirming] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH(
        "/api/llm/providers/{provider_id}/models/{model_id}",
        {
          params: {
            path: { provider_id: item.provider_id, model_id: item.id },
          },
          body: { model, priority },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      onSaved();
      onOpenChange(false);
    },
  });
  const requestClose = (next: boolean) => {
    if (next) {
      onOpenChange(true);
      return;
    }
    if (dirty) setConfirming(true);
    else onOpenChange(false);
  };
  const id = useId();
  return (
    <>
      <Dialog open={open} onOpenChange={requestClose}>
        <DialogContent className="max-w-md">
          <DialogTitle>编辑模型</DialogTitle>
          <DialogDescription>
            修改模型 ID 或调整优先级（数值越小越优先）。
          </DialogDescription>
          <form
            className="provider-form"
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <div className="form-grid">
              <div className="form-field">
                <label htmlFor={id + "model"}>模型 ID</label>
                <Input
                  id={id + "model"}
                  required
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                />
              </div>
              <div className="form-field">
                <label htmlFor={id + "priority"}>优先级</label>
                <Input
                  id={id + "priority"}
                  type="number"
                  min={0}
                  max={10000}
                  value={priority}
                  onChange={(event) => setPriority(Number(event.target.value))}
                />
              </div>
            </div>
            {save.isError && (
              <p className="field-error" role="alert">
                {save.error.message}
              </p>
            )}
            <div className="form-actions">
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? "保存中…" : "保存修改"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <UnsavedDialog
        open={confirming}
        onKeep={() => setConfirming(false)}
        onDiscard={() => {
          setConfirming(false);
          setModel(item.model);
          setPriority(item.priority);
          onOpenChange(false);
        }}
        returnFocus={trigger}
      />
    </>
  );
}
