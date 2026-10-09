import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, requireResponse } from "../../api/client";
import { Button, Input } from "../../components/ui";
import { showToast } from "../../components/Toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";
import { UnsavedDialog } from "../../components/UnsavedChanges";
import { PROTOCOLS, useFormDirty, type DirtyChange } from "./types";

/**
 * 新增供应商入口：弹层内填写名称与 Base URL，
 * 模型和 API Key 在创建后的详情面板中继续配置。
 */
export function ProviderCreateDialog({
  onSaved,
  onCreated,
  onDirtyChange,
}: {
  onSaved: () => void;
  onCreated: (providerId: string) => void;
  onDirtyChange: DirtyChange;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  // 显式标注 string：PROTOCOLS 是 as const，否则状态会被收窄成第一个字面量。
  const [protocol, setProtocol] = useState<string>(PROTOCOLS[0].value);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const queryClient = useQueryClient();
  const dirty =
    Boolean(name) || Boolean(baseUrl) || protocol !== PROTOCOLS[0].value;
  useFormDirty("provider-create", dirty && open, onDirtyChange);
  const create = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/llm/providers", {
        body: { name, base_url: baseUrl, timeout_seconds: 60, protocol },
      });
      const created = requireResponse(r.response, r.data, r.error);
      // 先等列表刷新完成再回调选中，避免页面 effect 用旧列表重置选择。
      await queryClient.invalidateQueries({ queryKey: ["providers"] });
      return created;
    },
    onSuccess: (created) => {
      setName("");
      setBaseUrl("");
      setProtocol(PROTOCOLS[0].value);
      setOpen(false);
      onSaved();
      onCreated(created.id);
      showToast({
        type: "success",
        content: `已新增供应商「${created.name}」。`,
      });
    },
  });
  const requestClose = (next: boolean) => {
    if (next) {
      setOpen(true);
      return;
    }
    if (dirty) {
      setConfirming(true);
    } else {
      setOpen(false);
    }
  };
  return (
    <>
      <Button ref={trigger} onClick={() => requestClose(true)}>
        添加 Provider
      </Button>
      <Dialog open={open} onOpenChange={requestClose}>
        <DialogContent className="max-w-md">
          <DialogTitle>添加 Provider</DialogTitle>
          <DialogDescription>
            先创建兼容服务连接，再在详情中添加模型与 API Key。
          </DialogDescription>
          <form
            className="provider-form"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate();
            }}
          >
            <label className="form-field" htmlFor="new-provider-name">
              名称
              <Input
                id="new-provider-name"
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：OpenCode"
              />
            </label>
            <label className="form-field" htmlFor="new-provider-url">
              兼容 API Base URL
              <Input
                id="new-provider-url"
                required
                type="url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://example.test/v1"
              />
            </label>
            <div className="form-field">
              <label htmlFor="new-provider-protocol">API 格式</label>
              <select
                id="new-provider-protocol"
                value={protocol}
                onChange={(event) => setProtocol(event.target.value)}
              >
                {PROTOCOLS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}（{item.endpoint}）
                  </option>
                ))}
              </select>
            </div>
            <div className="form-actions">
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "保存中…" : "添加 Provider"}
              </Button>
            </div>
            {create.isError && (
              <p className="field-error" role="alert">
                {create.error.message}
              </p>
            )}
          </form>
        </DialogContent>
      </Dialog>
      <UnsavedDialog
        open={confirming}
        onKeep={() => setConfirming(false)}
        onDiscard={() => {
          setConfirming(false);
          setName("");
          setBaseUrl("");
          setProtocol(PROTOCOLS[0].value);
          setOpen(false);
        }}
        returnFocus={trigger}
      />
    </>
  );
}
