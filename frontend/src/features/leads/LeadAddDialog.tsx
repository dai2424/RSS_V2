import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, requireResponse } from "../../api/client";
import { showToast } from "../../components/Toast";
import { Button, Input } from "../../components/ui";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "../../components/ui/sheet";

/** 加入线索的目标：只需要消息标识与展示用的标题。 */
export type LeadTarget = { id: string; title: string };

/**
 * 把选中的消息加进线索：可以选已有线索，也可以新建一条。
 *
 * 成员由服务端锁定为各自当前的最新版本；同一条消息重复加入会被跳过，
 * 因此这里不做去重，结果以服务端返回的 added 为准。
 */
export function LeadAddDialog({
  targets,
  onClose,
}: {
  targets: LeadTarget[] | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [leadId, setLeadId] = useState("");
  const ids = (targets ?? []).map((item) => item.id);
  const open = ids.length > 0;
  const leads = useQuery({
    enabled: open,
    queryKey: ["leads"],
    queryFn: async () => {
      const r = await api.GET("/api/leads", {
        params: { query: { limit: 100 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const finish = (content: string) => {
    showToast({ type: "success", content });
    void queryClient.invalidateQueries({ queryKey: ["leads"] });
    void queryClient.invalidateQueries({ queryKey: ["lead"] });
    setTitle("");
    setLeadId("");
    onClose();
  };
  const add = useMutation({
    mutationFn: async () => {
      if (leadId) {
        const r = await api.POST("/api/leads/{lead_id}/messages", {
          params: { path: { lead_id: leadId } },
          body: { message_ids: ids },
        });
        return requireResponse(r.response, r.data, r.error);
      }
      const r = await api.POST("/api/leads", {
        body: { title: title.trim(), message_ids: ids },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      const added = "added" in result ? result.added : ids.length;
      finish(
        added > 0 ? `已加入线索（新增 ${added} 条）。` : "这些消息已在线索里。",
      );
    },
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent side="bottom" className="sm:mx-auto sm:max-w-lg">
        <SheetTitle>加入线索</SheetTitle>
        <SheetDescription>
          将把选中的 {ids.length} 条消息加入线索，成员锁定为它们当前的最新版本；
          之后消息更新不会改写线索。
        </SheetDescription>
        <div className="form-row">
          <label htmlFor="lead-existing">已有线索</label>
          <select
            id="lead-existing"
            value={leadId}
            onChange={(e) => setLeadId(e.target.value)}
          >
            <option value="">（新建一条线索）</option>
            {leads.data?.items.map((lead) => (
              <option key={lead.id} value={lead.id}>
                {lead.title}（{lead.members} 条）
              </option>
            ))}
          </select>
        </div>
        {!leadId && (
          <div className="form-row">
            <label htmlFor="lead-title">线索名</label>
            <Input
              id="lead-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：某产品发布与首批评测"
            />
          </div>
        )}
        {leads.isError && (
          <div className="alert-error">
            线索列表读取失败：{leads.error.message}
          </div>
        )}
        <div className="toolbar">
          <Button
            className="secondary"
            onClick={onClose}
            disabled={add.isPending}
          >
            取消
          </Button>
          <Button
            disabled={add.isPending || (!leadId && title.trim().length === 0)}
            onClick={() => add.mutate()}
          >
            {add.isPending ? "加入中…" : "加入线索"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
