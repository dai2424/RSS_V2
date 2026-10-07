import { useMutation } from "@tanstack/react-query";
import { useState, useId } from "react";
import { api, requireResponse } from "../../api/client";
import { Button, ErrorState, Input } from "../../components/ui";
import { useFormDirty, type DirtyChange, type Key } from "./types";

/** 新增 Key 引用及优先级；实际凭据只能来自进程环境。 */
export function KeyForm({
  providerId,
  onSaved,
  onDirtyChange,
}: {
  providerId: string;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const id = useId();
  const [ref, setRef] = useState("");
  const [priority, setPriority] = useState(100);
  useFormDirty(
    "key-new-" + providerId,
    Boolean(ref) || priority !== 100,
    onDirtyChange,
  );
  const add = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/llm/providers/{provider_id}/keys", {
        params: { path: { provider_id: providerId } },
        body: { key_ref: ref, priority },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      setRef("");
      setPriority(100);
      onSaved();
    },
  });
  return (
    <form
      className="key-form"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <div className="inline-form">
        <label className="form-field" htmlFor={id + "ref"}>
          Key 引用名
          <Input
            id={id + "ref"}
            required
            pattern="[A-Za-z0-9_-]+"
            value={ref}
            onChange={(e) => setRef(e.target.value)}
          />
        </label>
        <label className="form-field" htmlFor={id + "prio"}>
          优先级
          <Input
            id={id + "prio"}
            type="number"
            min={0}
            max={10000}
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
          />
        </label>
        <Button type="submit" className="secondary" disabled={add.isPending}>
          添加引用
        </Button>
      </div>
      <p className="muted">
        先在 worker 环境配置 RSS_LLM_KEY_
        {ref.toUpperCase().replaceAll("-", "_") || "引用名"}，这里只填写引用名。
      </p>
      {add.isError && <ErrorState message={add.error.message} />}
    </form>
  );
}
/** 修改已有 Key 引用的优先级及启停状态。 */
export function KeyRow({
  item,
  onSaved,
  onDirtyChange,
}: {
  item: Key;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const [priority, setPriority] = useState(item.priority);
  useFormDirty("key-" + item.id, priority !== item.priority, onDirtyChange);
  const patch = useMutation({
    mutationFn: async (body: { enabled?: boolean; priority?: number }) => {
      const r = await api.PATCH(
        "/api/llm/providers/{provider_id}/keys/{key_id}",
        {
          params: { path: { provider_id: item.provider_id, key_id: item.id } },
          body,
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: onSaved,
  });
  return (
    <div>
      <div className="key-row">
        <code>{item.key_ref}</code>
        <span className="muted">
          {item.last_status || "未使用"}
          {item.cooldown_until && item.cooldown_until > Date.now() / 1000
            ? " · 冷却中"
            : ""}
        </span>
        <label className="form-field">
          优先级
          <Input
            aria-label={item.key_ref + " 优先级"}
            type="number"
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
          />
        </label>
        <Button
          className="secondary"
          disabled={patch.isPending}
          onClick={() => patch.mutate({ priority })}
        >
          保存优先级
        </Button>
        <Button
          className="secondary"
          disabled={patch.isPending}
          onClick={() => patch.mutate({ enabled: !item.enabled })}
        >
          {item.enabled ? "停用 Key" : "启用 Key"}
        </Button>
      </div>
      {patch.isError && <ErrorState message={patch.error.message} />}
    </div>
  );
}
