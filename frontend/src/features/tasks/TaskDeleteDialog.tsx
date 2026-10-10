import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import type { components } from "../../api/generated";
import { showToast } from "../../components/Toast";
import { Button } from "../../components/ui";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "../../components/ui/sheet";
import { statusLabels } from "../../lib/display";

type Task = components["schemas"]["TaskResponse"];

const TASK_LABELS: Record<string, string> = {
  collect_source: "采集 RSS",
  enrich_message: "内容加工",
  translate_message: "翻译消息",
};

/**
 * 删除任务确认。
 *
 * 只允许删已结束的任务：运行中的任务被删会让 worker 回写时取不到行，排队中的
 * collect 任务被删会让采集运行停在 running。因此这里的按钮对未结束任务禁用，
 * 后端还有一层状态校验兜底。
 */
export function TaskDeleteDialog({
  task,
  onClose,
}: {
  task: Task | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: async () => {
      const r = await api.DELETE("/api/tasks/{task_id}", {
        params: { path: { task_id: task!.id } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      showToast({
        type: "success",
        content: result.deleted
          ? `已删除任务 ${task?.id.slice(0, 8)}。`
          : "任务已经不在库里，列表已刷新。",
      });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  return (
    <Sheet open={Boolean(task)} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        side="bottom"
        role="alertdialog"
        className="sm:mx-auto sm:max-w-lg"
      >
        <SheetTitle>删除任务</SheetTitle>
        <SheetDescription>
          将删除任务 {task?.id.slice(0, 8)}（
          {TASK_LABELS[task?.task_type ?? ""] ?? ""}，
          {statusLabels[task?.status ?? ""]}
          ）。任务记录会消失，但已有的译文或加工结果、
          以及模型调用审计都会保留。此操作不可恢复。
        </SheetDescription>
        <div className="toolbar">
          <Button
            className="secondary"
            onClick={onClose}
            disabled={remove.isPending}
          >
            取消
          </Button>
          <Button
            className="danger"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            {remove.isPending ? "删除中…" : "确认删除"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** 清理失败任务：打开时先预览条数，确认后一次删完。 */
export function ClearFailedDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const preview = useQuery({
    enabled: open,
    queryKey: ["task-clear-failed"],
    queryFn: async () => {
      const r = await api.POST("/api/tasks/clear-failed", {
        body: { dry_run: true },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const clear = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/tasks/clear-failed", {
        body: { dry_run: false },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      showToast({
        type: "success",
        content: `已清理 ${result.deleted} 个失败任务。`,
      });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  const candidates = preview.data?.candidates ?? 0;
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        side="bottom"
        role="alertdialog"
        className="sm:mx-auto sm:max-w-lg"
      >
        <SheetTitle>清理失败任务</SheetTitle>
        <SheetDescription>
          {preview.isLoading
            ? "正在统计失败任务…"
            : candidates > 0
              ? `将删除 ${candidates} 个失败任务。成功任务、译文与加工结果、模型调用审计都会保留。`
              : "当前没有失败任务需要清理。"}
        </SheetDescription>
        {preview.isError && (
          <div className="alert-error">统计失败：{preview.error.message}</div>
        )}
        <div className="toolbar">
          <Button
            className="secondary"
            onClick={onClose}
            disabled={clear.isPending}
          >
            取消
          </Button>
          <Button
            className="danger"
            disabled={clear.isPending || candidates === 0}
            onClick={() => clear.mutate()}
          >
            {clear.isPending ? "清理中…" : `清理 ${candidates} 个任务`}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
