import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import { showToast } from "../../components/Toast";
import { Button } from "../../components/ui";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "../../components/ui/sheet";

/** 删除目标；只需要标识和名称，消息条数在弹层内单独查询。 */
export type DeleteTarget = { id: string; name: string };

/**
 * 删除来源确认：说明对象与影响（含已采集消息条数）后再执行，
 * 成功后提示并交给调用方处理列表刷新或跳转。
 */
export function SourceDeleteDialog({
  source,
  onClose,
  onDeleted,
}: {
  source: DeleteTarget | null;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    enabled: Boolean(source),
    queryKey: ["source", source?.id],
    queryFn: async () => {
      const r = await api.GET("/api/sources/{source_id}", {
        params: { path: { source_id: source!.id } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const remove = useMutation({
    mutationFn: async () => {
      const r = await api.DELETE("/api/sources/{source_id}", {
        params: { path: { source_id: source!.id } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      const name = source?.name ?? "";
      showToast({
        type: "success",
        content: result.deleted_messages
          ? `已删除来源「${name}」及其 ${result.deleted_messages} 条消息。`
          : `已删除来源「${name}」。`,
      });
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
      void queryClient.removeQueries({ queryKey: ["source", source?.id] });
      onClose();
      onDeleted?.();
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  const count = detail.data?.message_count;
  return (
    <Sheet open={Boolean(source)} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        side="bottom"
        role="alertdialog"
        className="sm:mx-auto sm:max-w-lg"
      >
        <SheetTitle>删除来源</SheetTitle>
        <SheetDescription>
          将删除「{source?.name}」
          {count === undefined
            ? "及其已采集的消息、版本、译文、健康记录与相关任务"
            : `及其已采集的 ${count} 条消息、版本、译文、健康记录与相关任务`}
          。此操作不可恢复；只想停止采集可改用「停用」。
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
