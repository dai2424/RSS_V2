import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import { Button } from "../../components/ui";
import { showToast } from "../../components/Toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";

/**
 * 维护操作：重建关键词索引与回填存量。
 *
 * 两者都会改动大量数据，因此先预览条数再执行；回填入队的是加工任务，消耗模型额度。
 */
export function KeywordMaintenance({ pending }: { pending: number }) {
  const [stage, setStage] = useState<"closed" | "preview" | "running">(
    "closed",
  );
  const [candidates, setCandidates] = useState(0);
  const queryClient = useQueryClient();

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
    await queryClient.invalidateQueries({ queryKey: ["keyword-vocabulary"] });
  };

  const backfill = useMutation({
    mutationFn: async (dryRun: boolean) => {
      const r = await api.POST("/api/keywords/backfill", {
        body: { dry_run: dryRun, limit: 200 },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });

  const rebuild = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/keywords/rebuild", {});
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async (data) => {
      await refresh();
      showToast({
        type: "success",
        content: `已按加工结果重建索引，写入 ${data.indexed} 行。`,
      });
    },
  });

  const openPreview = () => {
    backfill.mutate(true, {
      onSuccess: (data) => {
        setCandidates(data.candidates);
        setStage("preview");
      },
    });
  };

  const run = () =>
    backfill.mutate(false, {
      onSuccess: async (data) => {
        setStage("closed");
        await refresh();
        showToast({
          type: "success",
          content: (
            <>
              已创建 {data.enqueued} 个加工任务
              {data.skipped > 0 ? `，${data.skipped} 条跳过` : ""}，
              <Link to="/tasks">查看任务</Link>。
            </>
          ),
        });
      },
    });

  return (
    <>
      <div className="row-actions">
        <Button
          className="secondary"
          disabled={rebuild.isPending}
          onClick={() => rebuild.mutate()}
        >
          {rebuild.isPending ? "重建中…" : "重建索引"}
        </Button>
        <Button
          disabled={backfill.isPending}
          onClick={openPreview}
          title={
            pending === 0
              ? "没有待加工的存量消息"
              : `还有 ${pending} 条消息没有关键词`
          }
        >
          回填存量
        </Button>
      </div>
      {rebuild.isError && (
        <div className="alert-error">{rebuild.error.message}</div>
      )}
      {backfill.isError && (
        <div className="alert-error">{backfill.error.message}</div>
      )}
      <Dialog
        open={stage === "preview"}
        onOpenChange={(next) => (next ? undefined : setStage("closed"))}
      >
        <DialogContent>
          <DialogTitle>回填存量</DialogTitle>
          <DialogDescription>
            为还没有加工结果的消息创建加工任务，最多 200
            条一批；每条会调用一次模型。
            重建索引不调用模型，只把已有的加工结果重新写入关键词索引。
          </DialogDescription>
          <p>
            预览：本次将创建 <strong>{candidates}</strong> 个加工任务。
          </p>
          <div className="row-actions">
            <Button onClick={run} disabled={candidates === 0}>
              创建任务
            </Button>
            <Button className="secondary" onClick={() => setStage("closed")}>
              取消
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
