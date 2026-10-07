import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, requireResponse } from "../../api/client";
import type { components } from "../../api/generated";

type Source = components["schemas"]["SourceResponse"];

/** 行内操作的反馈消息；error 用警告样式并配合 role=alert 播报。 */
export type RowActionMessage = {
  text: string;
  tone: "info" | "error";
};

/** 来源列表行内操作：启停、测试、单源采集；结果消息统一显示在表格上方。 */
export function useSourceActions() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<RowActionMessage | null>(null);
  const [pendingId, setPendingId] = useState("");
  const run = (source: Source, action: () => Promise<string>) => {
    setPendingId(source.id);
    setMessage(null);
    action()
      .then((text) => {
        setMessage({ text, tone: "info" });
        void queryClient.invalidateQueries({ queryKey: ["sources"] });
      })
      .catch((error: unknown) => {
        setMessage({
          text: error instanceof Error ? error.message : "操作失败，请重试",
          tone: "error",
        });
      })
      .finally(() => setPendingId(""));
  };
  const toggle = (source: Source) =>
    run(source, async () => {
      const r = await api.PATCH("/api/sources/{source_id}", {
        params: { path: { source_id: source.id } },
        body: { enabled: !source.enabled },
      });
      const updated = requireResponse(r.response, r.data, r.error);
      return `「${updated.name}」已${updated.enabled ? "启用" : "停用"}。`;
    });
  const test = (source: Source) =>
    run(source, async () => {
      const r = await api.POST("/api/sources/{source_id}/test", {
        params: { path: { source_id: source.id } },
      });
      const result = requireResponse(r.response, r.data, r.error);
      return `「${source.name}」测试通过，解析到 ${result.health.entry_count} 条条目。`;
    });
  const collectOne = (source: Source) =>
    run(source, async () => {
      const r = await api.POST("/api/collection/runs", {
        body: { all_enabled: false, source_ids: [source.id] },
      });
      requireResponse(r.response, r.data, r.error);
      return `已为「${source.name}」创建采集任务，可在任务页查看进度。`;
    });
  return { message, pendingId, toggle, test, collect: collectOne };
}
