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

  const purge = useMutation({
    mutationFn: async (dryRun: boolean) => {
      const r = await api.POST("/api/keywords/purge-state", {
        body: { dry_run: dryRun },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const [purgeCounts, setPurgeCounts] = useState<{
    scanned: number;
    merge_suggestions: number;
    pin_suggestions: number;
  } | null>(null);

  const openPurgePreview = () => {
    purge.mutate(true, {
      onSuccess: (data) => {
        setPurgeCounts({
          scanned: data.scanned,
          merge_suggestions: data.merge_suggestions,
          pin_suggestions: data.pin_suggestions,
        });
      },
    });
  };
  const runPurge = () =>
    purge.mutate(false, {
      onSuccess: async (data) => {
        setPurgeCounts(null);
        await refresh();
        showToast({
          type: "success",
          content: `已清理扫描记账 ${data.scanned} 条、过期建议 ${data.merge_suggestions + data.pin_suggestions} 条。`,
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
          className="secondary"
          disabled={purge.isPending}
          onClick={openPurgePreview}
          title="清掉写法已经不在词表里的扫描记账与过期建议；不动消息关键词"
        >
          {purge.isPending ? "清理中…" : "清理词表状态"}
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
        open={purgeCounts !== null}
        onOpenChange={(next) => (next ? undefined : setPurgeCounts(null))}
      >
        <DialogContent>
          <DialogTitle>清理词表状态</DialogTitle>
          <DialogDescription>
            清掉写法已经不在词表里的扫描记账，以及目标词已经消失的待处理建议。
            消息级关键词不动——它是消息的元数据，删了会被「重建索引」恢复。
          </DialogDescription>
          <p>
            预览：扫描记账 <strong>{purgeCounts?.scanned ?? 0}</strong>{" "}
            条、合并建议 <strong>{purgeCounts?.merge_suggestions ?? 0}</strong>{" "}
            条、常驻建议 <strong>{purgeCounts?.pin_suggestions ?? 0}</strong>{" "}
            条。
          </p>
          <div className="row-actions">
            <Button
              onClick={runPurge}
              disabled={
                !purgeCounts ||
                purgeCounts.scanned +
                  purgeCounts.merge_suggestions +
                  purgeCounts.pin_suggestions ===
                  0
              }
            >
              确认清理
            </Button>
            <Button className="secondary" onClick={() => setPurgeCounts(null)}>
              取消
            </Button>
          </div>
        </DialogContent>
      </Dialog>
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
