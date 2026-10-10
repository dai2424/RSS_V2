import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, requireResponse } from "../../api/client";
import { Badge, Button, Card, EmptyState } from "../../components/ui";
import { showToast } from "../../components/Toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";
import { formatTime } from "../../lib/display";

/** 合并弹层：选目标词 → 看影响面 → 确认。提示里写清"只合并同指"。 */
export function MergeDialog({
  open,
  selected,
  onClose,
}: {
  open: boolean;
  selected: { key: string; text: string; mentions: number }[];
  onClose: () => void;
}) {
  const [target, setTarget] = useState("");
  const queryClient = useQueryClient();
  const targetKey = target || selected[0]?.key || "";
  const candidates = selected.filter((item) => item.key !== targetKey);
  const preview = useQuery({
    queryKey: [
      "keyword-merge-preview",
      targetKey,
      selected.map((item) => item.key).join(","),
    ],
    enabled: open && candidates.length > 0,
    queryFn: async () => {
      const r = await api.POST("/api/keywords/merge", {
        body: {
          keys: candidates.map((item) => item.key),
          target: targetKey,
          dry_run: true,
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const merge = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/keywords/merge", {
        body: {
          keys: candidates.map((item) => item.key),
          target: targetKey,
          dry_run: false,
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["keyword-vocabulary"] });
      await queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["keyword-merges"] });
      showToast({ type: "success", content: "已合并，可在合并记录里撤销。" });
      onClose();
    },
  });
  const forms = preview.data?.preview.forms ?? [];
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent>
        <DialogTitle>合并关键词</DialogTitle>
        <DialogDescription>
          把指同一事物的写法并成一个词条。不要合并上下位关系（例如 尊界 与
          尊界V800）， 那会抹平检索粒度。
        </DialogDescription>
        <label htmlFor="merge-target">保留哪一个写法</label>
        <select
          id="merge-target"
          className="input"
          value={targetKey}
          onChange={(event) => setTarget(event.target.value)}
        >
          {selected.map((item) => (
            <option key={item.key} value={item.key}>
              {item.text}（出现 {item.mentions} 次）
            </option>
          ))}
        </select>
        {candidates.length === 0 ? (
          <div className="alert">至少选择两个词才能合并。</div>
        ) : preview.isError ? (
          <div className="alert-error">预览失败：{preview.error.message}</div>
        ) : (
          <div className="stack">
            <p>
              将并入 {candidates.length} 个词条，合并后有 {forms.length}{" "}
              种写法、 共 {preview.data?.preview.mentions ?? 0} 个词位，影响{" "}
              {preview.data?.preview.messages ?? 0} 条消息。
            </p>
            <span className="badge-row">
              {forms.map((form) => (
                <Badge key={form.text} tone="neutral">
                  {form.text}
                </Badge>
              ))}
            </span>
          </div>
        )}
        <div className="row-actions">
          <Button
            onClick={() => merge.mutate()}
            disabled={
              candidates.length === 0 || merge.isPending || preview.isLoading
            }
          >
            {merge.isPending ? "合并中…" : "确认合并"}
          </Button>
          <Button className="secondary" onClick={onClose}>
            取消
          </Button>
        </div>
        {merge.isError && (
          <div className="alert-error">{merge.error.message}</div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 合并记录：生效中的可以撤销；已撤销的保留展示。 */
export function MergeHistory() {
  const queryClient = useQueryClient();
  const records = useQuery({
    queryKey: ["keyword-merges"],
    queryFn: async () => {
      const r = await api.GET("/api/keywords/merges", {
        params: { query: { limit: 10 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const undo = useMutation({
    mutationFn: async (mergeId: string) => {
      const r = await api.POST("/api/keywords/merges/{merge_id}/undo", {
        params: { path: { merge_id: mergeId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["keyword-vocabulary"] });
      await queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["keyword-merges"] });
      showToast({
        type: "info",
        content: "已撤销这次合并，写法回到各自词条。",
      });
    },
  });
  const items = records.data ?? [];
  return (
    <Card className="card-pad">
      <h2>合并记录</h2>
      {items.length === 0 ? (
        <EmptyState
          title="还没有合并记录"
          description="在上面的词表里勾选两个以上同指写法即可合并。"
        />
      ) : (
        <ul className="stack">
          {items.map((item) => (
            <li key={item.id}>
              <div className="split">
                <div>
                  <span className="cell-title">
                    {item.target_raw} ←{" "}
                    {item.members.map((member) => member.text).join("、")}
                  </span>
                  <span className="cell-subtitle">
                    {formatTime(item.created_at)} · 影响 {item.messages} 条消息
                  </span>
                </div>
                {item.undone_at ? (
                  <span className="muted">
                    已于 {formatTime(item.undone_at)} 撤销
                  </span>
                ) : (
                  <Button
                    className="danger-outline"
                    disabled={undo.isPending}
                    onClick={() => undo.mutate(item.id)}
                  >
                    撤销
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {undo.isError && <div className="alert-error">{undo.error.message}</div>}
    </Card>
  );
}
