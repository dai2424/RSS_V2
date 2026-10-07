import { useMutation } from "@tanstack/react-query";
import { useState, useRef, useCallback } from "react";
import { api, requireResponse } from "../../api/client";
import { Badge, Button, Card, ErrorState } from "../../components/ui";
import { UnsavedDialog } from "../../components/UnsavedChanges";
import { ProviderForm } from "./ProviderForm";
import { KeyForm, KeyRow } from "./KeySettings";
import type { DirtyChange, Provider } from "./types";

/** 展示一个 Provider 与 Key 池，关闭有修改的配置前确认。 */
export function ProviderCard({
  provider,
  onSaved,
  onDirtyChange,
}: {
  provider: Provider;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const [editing, setEditing] = useState(false);
  const [formDirty, setFormDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  const trackForm = useCallback<DirtyChange>(
    (id, dirty) => {
      setFormDirty(dirty);
      onDirtyChange(id, dirty);
    },
    [onDirtyChange],
  );
  const closeEditor = () => {
    if (editing && formDirty) {
      if (document.activeElement instanceof HTMLElement)
        returnFocus.current = document.activeElement;
      setConfirmClose(true);
    } else setEditing(!editing);
  };
  const toggle = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH("/api/llm/providers/{provider_id}", {
        params: { path: { provider_id: provider.id } },
        body: { enabled: !provider.enabled },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: onSaved,
  });
  return (
    <Card className="card-pad" role="region" aria-label={provider.name}>
      <div className="split">
        <div>
          <h2>{provider.name}</h2>
          <div className="muted">
            {provider.base_url} · {provider.model} · 优先级 {provider.priority}
          </div>
        </div>
        <div className="toolbar">
          <Badge tone={provider.enabled ? "success" : "neutral"}>
            {provider.enabled ? "已启用" : "已停用"}
          </Badge>
          <Button className="secondary" onClick={closeEditor}>
            {editing ? "关闭编辑" : "编辑"}
          </Button>
          <Button
            className="secondary"
            disabled={toggle.isPending}
            onClick={() => toggle.mutate()}
          >
            {provider.enabled ? "停用" : "启用"}
          </Button>
        </div>
      </div>
      {toggle.isError && <ErrorState message={toggle.error.message} />}
      {editing && (
        <ProviderForm
          provider={provider}
          onSaved={onSaved}
          onDirtyChange={trackForm}
        />
      )}
      <div className="key-list">
        {provider.keys.map((key) => (
          <KeyRow
            key={key.id}
            item={key}
            onSaved={onSaved}
            onDirtyChange={onDirtyChange}
          />
        ))}
      </div>
      <KeyForm
        providerId={provider.id}
        onSaved={onSaved}
        onDirtyChange={onDirtyChange}
      />
      <UnsavedDialog
        open={confirmClose}
        onKeep={() => setConfirmClose(false)}
        onDiscard={() => {
          setConfirmClose(false);
          setEditing(false);
        }}
        returnFocus={returnFocus}
      />
    </Card>
  );
}
