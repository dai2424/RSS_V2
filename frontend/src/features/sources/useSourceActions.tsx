import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, requireResponse } from "../../api/client";
import { showToast, type ToastType } from "../../components/Toast";
import type { components } from "../../api/generated";

type Source = components["schemas"]["SourceResponse"];

/** 采集类操作的公共提示尾部，链接直达任务页。 */
function collectNotice(name: string): ReactNode {
  return (
    <>
      已为「{name}」创建采集任务，<Link to="/tasks">查看任务</Link>。
    </>
  );
}

/** 来源列表行内操作：启停、测试、单源采集；反馈走顶部浮层提示。 */
export function useSourceActions() {
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState("");
  const run = (
    source: Source,
    action: () => Promise<{ type: ToastType; content: ReactNode }>,
  ) => {
    setPendingId(source.id);
    action()
      .then((toast) => {
        showToast(toast);
        void queryClient.invalidateQueries({ queryKey: ["sources"] });
      })
      .catch((error: unknown) => {
        showToast({
          type: "error",
          content: error instanceof Error ? error.message : "操作失败，请重试",
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
      return {
        type: "success" as const,
        content: `「${updated.name}」已${updated.enabled ? "启用" : "停用"}。`,
      };
    });
  const test = (source: Source) =>
    run(source, async () => {
      const r = await api.POST("/api/sources/{source_id}/test", {
        params: { path: { source_id: source.id } },
      });
      const result = requireResponse(r.response, r.data, r.error);
      return {
        type: "success" as const,
        content: `「${source.name}」测试通过，解析到 ${result.health.entry_count} 条条目。`,
      };
    });
  const collect = (source: Source) =>
    run(source, async () => {
      const r = await api.POST("/api/collection/runs", {
        body: { all_enabled: false, source_ids: [source.id] },
      });
      requireResponse(r.response, r.data, r.error);
      return { type: "info" as const, content: collectNotice(source.name) };
    });
  return { pendingId, toggle, test, collect };
}
