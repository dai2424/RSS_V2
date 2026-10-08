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
import {
  keyStatusLabel,
  useFormDirty,
  type DirtyChange,
  type Key,
} from "./types";

/** 密钥输入：默认遮蔽，可切换明文核对后再提交。 */
function SecretInput({
  id,
  value,
  required,
  onChange,
}: {
  id: string;
  value: string;
  required?: boolean;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="secret-input">
      <Input
        id={id}
        required={required}
        type={visible ? "text" : "password"}
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <Button
        className="secondary"
        aria-label={visible ? "隐藏 API Key" : "显示 API Key"}
        aria-pressed={visible}
        onClick={() => setVisible(!visible)}
      >
        {visible ? "隐藏" : "显示"}
      </Button>
    </div>
  );
}

/** 区块标题右侧的“添加 Key”入口：密钥值随提交写入本机数据库。 */
export function KeyAddDialog({
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
  const [secret, setSecret] = useState("");
  const [priority, setPriority] = useState(100);
  const trigger = useRef<HTMLButtonElement | null>(null);
  useFormDirty("key-new-" + providerId, Boolean(secret), onDirtyChange);
  const add = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/llm/providers/{provider_id}/keys", {
        params: { path: { provider_id: providerId } },
        body: { secret, priority },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      setSecret("");
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
    if (secret) setConfirming(true);
    else setOpen(false);
  };
  return (
    <>
      <Button
        ref={trigger}
        className="secondary"
        onClick={() => requestClose(true)}
      >
        添加 Key
      </Button>
      <Dialog open={open} onOpenChange={requestClose}>
        <DialogContent className="max-w-md">
          <DialogTitle>添加 API Key</DialogTitle>
          <DialogDescription>
            密钥只保存在本机运行目录数据库，界面、日志与调用记录只显示末四位掩码。
          </DialogDescription>
          <form
            className="provider-form"
            onSubmit={(event) => {
              event.preventDefault();
              add.mutate();
            }}
          >
            <div className="form-field">
              <label htmlFor={id + "secret"}>API Key</label>
              <SecretInput
                id={id + "secret"}
                required
                value={secret}
                onChange={setSecret}
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
              <Button type="submit" disabled={add.isPending || !secret.trim()}>
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
          setSecret("");
          setOpen(false);
        }}
        returnFocus={trigger}
      />
    </>
  );
}

/** 单枚 Key：只显示掩码、最近状态与优先级；编辑与启停在右侧。 */
export function KeyRow({ item, onSaved }: { item: Key; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const toggle = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH(
        "/api/llm/providers/{provider_id}/keys/{key_id}",
        {
          params: { path: { provider_id: item.provider_id, key_id: item.id } },
          body: { enabled: !item.enabled },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: onSaved,
  });
  return (
    <div className="key-row">
      <code>{item.masked}</code>
      <span className="muted">
        优先级 {item.priority} ·{" "}
        {keyStatusLabel(item, Math.floor(Date.now() / 1000))}
      </span>
      <div className="row-actions">
        <Button className="secondary" onClick={() => setEditing(true)}>
          编辑
        </Button>
        <Button
          className="secondary"
          disabled={toggle.isPending}
          onClick={() => toggle.mutate()}
        >
          {item.enabled ? "停用 Key" : "启用 Key"}
        </Button>
      </div>
      {toggle.isError && (
        <p className="field-error" role="alert">
          {toggle.error.message}
        </p>
      )}
      {editing && (
        <KeyEditDialog
          item={item}
          open={editing}
          onOpenChange={setEditing}
          onSaved={onSaved}
        />
      )}
    </div>
  );
}

/** 编辑弹层：调整优先级，或填写新值替换密钥（留空表示只改优先级）。 */
function KeyEditDialog({
  item,
  open,
  onOpenChange,
  onSaved,
}: {
  item: Key;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const id = useId();
  const [secret, setSecret] = useState("");
  const [priority, setPriority] = useState(item.priority);
  const save = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH(
        "/api/llm/providers/{provider_id}/keys/{key_id}",
        {
          params: { path: { provider_id: item.provider_id, key_id: item.id } },
          body: secret.trim() ? { secret, priority } : { priority },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      setSecret("");
      onOpenChange(false);
      onSaved();
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogTitle>编辑 API Key</DialogTitle>
        <DialogDescription>
          当前密钥 {item.masked}；如需更换请填写新值，留空则只保存优先级。
        </DialogDescription>
        <form
          className="provider-form"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <div className="form-field">
            <label htmlFor={id + "secret"}>新 API Key（可选）</label>
            <SecretInput
              id={id + "secret"}
              value={secret}
              onChange={setSecret}
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
          {save.isError && (
            <p className="field-error" role="alert">
              {save.error.message}
            </p>
          )}
          <div className="form-actions">
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "保存中…" : "保存"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
