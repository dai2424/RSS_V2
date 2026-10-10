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

/** 删除目标：只需要标识与展示用的标题。 */
export type DeleteTarget = { id: string; title: string };

/**
 * 删除消息确认：说明对象与影响（版本、译文、加工结果、任务）后再执行。
 *
 * 单条走 `GET /impact`，多条走 `POST /bulk-delete` 的 dry_run，两种场景拿到同一形状的影响面，
 * 因此文案与确认流程只有一份。
 */
export function MessageDeleteDialog({
  targets,
  onClose,
}: {
  targets: DeleteTarget[] | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const ids = (targets ?? []).map((item) => item.id);
  const impact = useQuery({
    enabled: ids.length > 0,
    queryKey: ["message-impact", ids.join(",")],
    queryFn: async () => {
      if (ids.length === 1) {
        const r = await api.GET("/api/messages/{message_id}/impact", {
          params: { path: { message_id: ids[0] } },
        });
        return requireResponse(r.response, r.data, r.error);
      }
      const r = await api.POST("/api/messages/bulk-delete", {
        body: { message_ids: ids, dry_run: true },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const remove = useMutation({
    mutationFn: async () => {
      if (ids.length === 1) {
        const r = await api.DELETE("/api/messages/{message_id}", {
          params: { path: { message_id: ids[0] } },
        });
        return requireResponse(r.response, r.data, r.error);
      }
      const r = await api.POST("/api/messages/bulk-delete", {
        body: { message_ids: ids, dry_run: false },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      showToast({
        type: "success",
        content: `已删除 ${result.messages} 条消息。`,
      });
      void queryClient.invalidateQueries({ queryKey: ["messages"] });
      // 关键词统计与词表随消息变化，一并刷新。
      void queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
      void queryClient.invalidateQueries({ queryKey: ["keyword-vocabulary"] });
      onClose();
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  const data = impact.data;
  const single = targets?.length === 1 ? targets[0] : undefined;
  return (
    <Sheet open={ids.length > 0} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        side="bottom"
        role="alertdialog"
        className="sm:mx-auto sm:max-w-lg"
      >
        <SheetTitle>删除消息</SheetTitle>
        <SheetDescription>
          {single
            ? `将删除「${single.title}」`
            : `将删除选中的 ${ids.length} 条消息`}
          {data
            ? `及其 ${data.versions} 个版本、${data.translations} 条译文、${data.enrichments} 条加工结果与 ${data.tasks} 个任务`
            : "及其版本、译文、加工结果与相关任务"}
          。此操作不可恢复；这些条目在来源下次采集时可能再次出现，需要彻底清理请改用删除来源。
        </SheetDescription>
        {impact.isError && (
          <div className="alert-error">
            影响面读取失败：{impact.error.message}
          </div>
        )}
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
            disabled={remove.isPending || impact.isLoading}
            onClick={() => remove.mutate()}
          >
            {remove.isPending ? "删除中…" : "确认删除"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
