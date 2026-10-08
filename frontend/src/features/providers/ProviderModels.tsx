import { useMutation } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { api, requireResponse } from "../../api/client";
import { Button, Input } from "../../components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";
import { UnsavedDialog } from "../../components/UnsavedChanges";
import { ModelRow } from "./ProviderModelRow";
import { useFormDirty, type DirtyChange, type ProviderModel } from "./types";

/** Provider 详情中的模型列表：行内测试与启停，追加走弹层。 */
export function ProviderModels({
  providerId,
  models,
  testingModel,
  onTest,
  onSaved,
  onDirtyChange,
}: {
  providerId: string;
  models: ProviderModel[];
  testingModel: string | null;
  onTest: (model: string) => void;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  return (
    <section className="provider-section">
      <div className="provider-section-head">
        <h3>模型列表</h3>
        <ModelAddDialog
          providerId={providerId}
          onSaved={onSaved}
          onDirtyChange={onDirtyChange}
        />
      </div>
      {models.length === 0 ? (
        <p className="muted">暂无模型；添加模型 ID 后才能创建翻译任务。</p>
      ) : (
        <div className="provider-rows">
          {models.map((model) => (
            <ModelRow
              key={model.id}
              item={model}
              testing={testingModel === model.model}
              onTest={() => onTest(model.model)}
              onSaved={onSaved}
              onDirtyChange={onDirtyChange}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/** 追加模型弹层：模型 ID 与优先级；提交成功后刷新列表。 */
function ModelAddDialog({
  providerId,
  onSaved,
  onDirtyChange,
}: {
  providerId: string;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [model, setModel] = useState("");
  const [priority, setPriority] = useState(100);
  const trigger = useRef<HTMLButtonElement | null>(null);
  useFormDirty("model-new-" + providerId, Boolean(model), onDirtyChange);
  const add = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/llm/providers/{provider_id}/models", {
        params: { path: { provider_id: providerId } },
        body: { model, priority },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      setModel("");
      setPriority(100);
      setOpen(false);
      onSaved();
    },
  });
  const requestClose = (next: boolean) => {
    if (next) {
      setOpen(true);
      return;
    }
    if (model) setConfirming(true);
    else setOpen(false);
  };
  return (
    <>
      <Button
        ref={trigger}
        className="secondary"
        onClick={() => requestClose(true)}
      >
        添加模型
      </Button>
      <Dialog open={open} onOpenChange={requestClose}>
        <DialogContent className="max-w-md">
          <DialogTitle>添加模型</DialogTitle>
          <DialogDescription>
            模型 ID 由兼容服务提供，例如 deepseek-v4.1-flash。
          </DialogDescription>
          <form
            className="provider-form"
            onSubmit={(event) => {
              event.preventDefault();
              add.mutate();
            }}
          >
            <div className="form-field">
              <label htmlFor={id + "model"}>模型 ID</label>
              <Input
                id={id + "model"}
                required
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="例如：deepseek-v4.1-flash"
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
            {add.isError && (
              <p className="field-error" role="alert">
                {add.error.message}
              </p>
            )}
            <div className="form-actions">
              <Button type="submit" disabled={add.isPending || !model.trim()}>
                {add.isPending ? "保存中…" : "添加"}
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
          setModel("");
          setOpen(false);
        }}
        returnFocus={trigger}
      />
    </>
  );
}
