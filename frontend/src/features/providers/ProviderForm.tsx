import { useMutation } from "@tanstack/react-query";
import { useState, useId, useRef } from "react";
import { z } from "zod";
import { api, requireResponse } from "../../api/client";
import { Button, ErrorState, Input } from "../../components/ui";
import { useFormDirty, type DirtyChange, type Provider } from "./types";

/** 编辑 Provider 非敏感配置，保存失败保留当前输入。 */
export function ProviderForm({
  provider,
  onSaved,
  onDirtyChange,
}: {
  provider?: Provider;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const id = useId();
  const [form, setForm] = useState({
    name: provider?.name || "",
    base_url: provider?.base_url || "",
    model: provider?.model || "",
    priority: provider?.priority ?? 100,
    timeout_seconds: provider?.timeout_seconds ?? 60,
    session_header_name: provider?.session_header_name || "",
    extra_headers: JSON.stringify(provider?.extra_headers || {}),
  });
  const baseline = useRef(JSON.stringify(form));
  useFormDirty(
    "provider-" + (provider?.id || "new"),
    JSON.stringify(form) !== baseline.current,
    onDirtyChange,
  );
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        ...form,
        session_header_name: form.session_header_name || null,
        extra_headers: z
          .record(z.string(), z.string())
          .parse(JSON.parse(form.extra_headers)),
      };
      const r = provider
        ? await api.PATCH("/api/llm/providers/{provider_id}", {
            params: { path: { provider_id: provider.id } },
            body,
          })
        : await api.POST("/api/llm/providers", { body });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      onSaved();
      const next = provider ? { ...form } : { ...form, name: "" };
      baseline.current = JSON.stringify(next);
      setForm(next);
    },
  });
  const fields = [
    ["name", "名称"],
    ["base_url", "兼容 API Base URL"],
    ["model", "模型"],
    ["priority", "优先级"],
    ["timeout_seconds", "超时（秒）"],
    ["session_header_name", "会话头名称（可选）"],
    ["extra_headers", "非敏感请求头 JSON"],
  ] as const;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="form-grid">
        {fields.map(([field, label]) => (
          <div className="form-field" key={field}>
            <label htmlFor={id + field}>{label}</label>
            <Input
              id={id + field}
              required={!["session_header_name"].includes(field)}
              type={
                field === "priority" || field === "timeout_seconds"
                  ? "number"
                  : "text"
              }
              value={form[field]}
              onChange={(e) =>
                setForm({
                  ...form,
                  [field]:
                    field === "priority" || field === "timeout_seconds"
                      ? Number(e.target.value)
                      : e.target.value,
                })
              }
            />
          </div>
        ))}
      </div>
      {save.isError && <ErrorState message={save.error.message} />}
      {save.isSuccess && (
        <p className="muted" role="status">
          配置已保存。
        </p>
      )}
      <div className="form-actions">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "保存中…" : provider ? "保存修改" : "添加 Provider"}
        </Button>
      </div>
    </form>
  );
}
