import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { z } from "zod";
import { api, requireResponse } from "../../api/client";
import { Button, Input } from "../../components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";
import { UnsavedDialog } from "../../components/UnsavedChanges";
import { useFormDirty, type DirtyChange, type Provider } from "./types";

const headersSchema = z.record(z.string(), z.string());

/**
 * 编辑供应商连接配置的弹层表单；保存失败保留输入，关闭有修改时确认。
 * fields: name / base_url / timeout_seconds / session_header_name / extra_headers。
 */
export function ProviderEditDialog({
  provider,
  open,
  onOpenChange,
  onSaved,
  onDirtyChange,
}: {
  provider: Provider;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const [form, setForm] = useState(() => initialForm(provider));
  const baseline = useRef(JSON.stringify(form));
  useFormDirty(
    "provider-edit-" + provider.id,
    open && JSON.stringify(form) !== baseline.current,
    onDirtyChange,
  );
  const [confirming, setConfirming] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH("/api/llm/providers/{provider_id}", {
        params: { path: { provider_id: provider.id } },
        body: {
          ...form,
          session_header_name: form.session_header_name || null,
          extra_headers: headersSchema.parse(JSON.parse(form.extra_headers)),
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      baseline.current = JSON.stringify(form);
      onSaved();
      onOpenChange(false);
    },
  });
  const requestClose = (next: boolean) => {
    if (next) {
      onOpenChange(true);
      return;
    }
    if (JSON.stringify(form) !== baseline.current) {
      setConfirming(true);
    } else {
      onOpenChange(false);
    }
  };
  const fields = [
    ["name", "名称", "text"],
    ["base_url", "兼容 API Base URL", "text"],
    ["timeout_seconds", "超时（秒）", "number"],
    ["session_header_name", "会话头名称（可选）", "text"],
    ["extra_headers", "非敏感请求头 JSON", "text"],
  ] as const;
  return (
    <>
      <Dialog open={open} onOpenChange={requestClose}>
        <DialogContent className="max-w-2xl">
          <DialogTitle>编辑连接配置</DialogTitle>
          <DialogDescription>
            {provider.name} · 保存后立即对后续翻译任务生效。
          </DialogDescription>
          <form
            className="provider-form"
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <div className="form-grid">
              {fields.map(([field, label, type]) => (
                <div
                  className={
                    field === "base_url" || field === "extra_headers"
                      ? "form-field full"
                      : "form-field"
                  }
                  key={field}
                >
                  <label htmlFor={"edit-" + field}>{label}</label>
                  <Input
                    id={"edit-" + field}
                    required={field !== "session_header_name"}
                    type={type}
                    placeholder={
                      field === "session_header_name"
                        ? "例如：x-opencode-session"
                        : undefined
                    }
                    value={form[field]}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        [field]:
                          type === "number"
                            ? Number(event.target.value)
                            : event.target.value,
                      })
                    }
                  />
                </div>
              ))}
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
          const restored = initialForm(provider);
          setForm(restored);
          baseline.current = JSON.stringify(restored);
          onOpenChange(false);
        }}
        returnFocus={trigger}
      />
    </>
  );
}

function initialForm(provider: Provider) {
  return {
    name: provider.name,
    base_url: provider.base_url,
    timeout_seconds: provider.timeout_seconds,
    session_header_name: provider.session_header_name || "",
    extra_headers: JSON.stringify(provider.extra_headers || {}),
  };
}
