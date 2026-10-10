import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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

/** 可清理的两个终态；其余状态的任务不允许删除。 */
const CLEARABLE = [
  { status: "failed", label: "失败的任务" },
  { status: "succeeded", label: "已完成的任务" },
] as const;

type ClearableStatus = (typeof CLEARABLE)[number]["status"];

/**
 * 清理已结束的任务：先按状态看条数，再一次性删完。
 *
 * 默认只勾"失败的任务"——清理已完成任务是批量动作，不该一进来就选中。
 * 排队与运行中的任务不在选项里，后端也只接受终态取值。
 */
export function ClearTasksDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<ClearableStatus[]>(["failed"]);
  const preview = useQuery({
    enabled: open,
    queryKey: ["task-clear-preview"],
    queryFn: async () => {
      // 每个状态单独问一次条数，界面上才能分开展示。
      const counts = await Promise.all(
        CLEARABLE.map(async (item) => {
          const r = await api.POST("/api/tasks/clear", {
            body: { statuses: [item.status], dry_run: true },
          });
          return requireResponse(r.response, r.data, r.error).candidates;
        }),
      );
      return Object.fromEntries(
        CLEARABLE.map((item, index) => [item.status, counts[index]]),
      ) as Record<string, number>;
    },
  });
  const clear = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/tasks/clear", {
        body: { statuses: chosen, dry_run: false },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      showToast({
        type: "success",
        content: `已清理 ${result.deleted} 个任务。`,
      });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onClose();
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  const target = chosen.reduce(
    (sum, status) => sum + (preview.data?.[status] ?? 0),
    0,
  );
  const toggle = (status: ClearableStatus) =>
    setChosen((current) =>
      current.includes(status)
        ? current.filter((item) => item !== status)
        : [...current, status],
    );
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        side="bottom"
        role="alertdialog"
        className="sm:mx-auto sm:max-w-lg"
      >
        <SheetTitle>清理任务</SheetTitle>
        <SheetDescription>
          只清理已经结束的任务；排队与运行中的任务不会被删除。译文、加工结果与模型调用审计都会保留。
        </SheetDescription>
        {CLEARABLE.map((item) => (
          <label key={item.status} className="check-row">
            <input
              type="checkbox"
              checked={chosen.includes(item.status)}
              onChange={() => toggle(item.status)}
            />
            {item.label}
            <span className="muted">
              {preview.isLoading
                ? "统计中…"
                : `${preview.data?.[item.status] ?? 0} 个`}
            </span>
          </label>
        ))}
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
            disabled={clear.isPending || chosen.length === 0 || target === 0}
            onClick={() => clear.mutate()}
          >
            {clear.isPending ? "清理中…" : `清理 ${target} 个任务`}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
