import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import { showToast } from "../../components/Toast";
import { sizeParams, usePaginationParams } from "../../lib/usePaginationParams";

/** 关键词页的数据读取与词表操作：概览、词表、合并记录；筛选状态全部写在 URL 上。 */
export function useKeywordOverview() {
  return useQuery({
    queryKey: ["keyword-overview"],
    queryFn: async () => {
      const r = await api.GET("/api/keywords/overview", {
        params: { query: { days: 14, top_sources: 10 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function useKeywordVocabulary() {
  const { params, setParams, size, offset, setPageOffset, setSize } =
    usePaginationParams();
  const q = params.get("q") || "";
  const kind = params.get("kind") || "";
  const minCount = Number(params.get("min") || 2);
  const pinned = params.get("pinned") === "1";

  const list = useQuery({
    queryKey: ["keyword-vocabulary", q, kind, minCount, pinned, size, offset],
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/keywords", {
        params: {
          query: {
            q: q || undefined,
            kind: kind || undefined,
            min_count: minCount,
            pinned: pinned || undefined,
            limit: size,
            offset,
          },
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next, { replace: true });
  };
  const clear = () => setParams(sizeParams(size), { replace: true });

  return {
    params,
    q,
    kind,
    minCount,
    pinned,
    size,
    offset,
    list,
    setFilter,
    setPageOffset,
    setSize,
    clear,
  };
}

/** 标记或取消常驻；成功后刷新词表与概览，让开关反映服务端状态。 */
export function useSetKeywordPin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { key: string; pinned: boolean }) => {
      const r = await api.POST("/api/keywords/pin", { body: input });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async (_data, input) => {
      await queryClient.invalidateQueries({ queryKey: ["keyword-vocabulary"] });
      await queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
      showToast({
        type: "success",
        content: input.pinned
          ? "已标记常驻，注入提示词时必然出现。"
          : "已取消常驻。",
      });
    },
    onError: (error: Error) => {
      showToast({ type: "error", content: error.message });
    },
  });
}

/** 合并建议：待确认列表、生成下一批、忽略与采纳标记。 */
export function useKeywordSuggestions() {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["keyword-suggestions"],
    queryFn: async () => {
      const r = await api.GET("/api/keywords/suggestions", {
        params: { query: { status: "pending", limit: 50 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["keyword-suggestions"] });
    await queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
  };
  const generate = useMutation({
    // 不带偏移：服务端接着上一批往后走，刷新页面也不会从头重复。
    mutationFn: async () => {
      const r = await api.POST("/api/keywords/suggestions", { body: {} });
      const data = requireResponse(r.response, r.data, r.error);
      await refresh();
      return data;
    },
    onError: (error: Error) => {
      showToast({ type: "error", content: error.message });
    },
  });
  const dismiss = useMutation({
    mutationFn: async (suggestionId: string) => {
      const r = await api.POST(
        "/api/keywords/suggestions/{suggestion_id}/dismiss",
        {
          params: { path: { suggestion_id: suggestionId } },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async () => {
      await refresh();
      showToast({ type: "success", content: "已忽略这组建议，不会再出现。" });
    },
    onError: (error: Error) => {
      showToast({ type: "error", content: error.message });
    },
  });
  const accept = useMutation({
    mutationFn: async (input: { id: string; mergeId: string }) => {
      const r = await api.POST(
        "/api/keywords/suggestions/{suggestion_id}/accept",
        {
          params: { path: { suggestion_id: input.id } },
          body: { merge_id: input.mergeId },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async () => {
      await refresh();
    },
    onError: (error: Error) => {
      // 合并已经完成，只是标记失败：提示人工忽略这条建议即可。
      showToast({
        type: "error",
        content: `合并已完成，但建议状态没更新（${error.message}），可以点忽略收尾。`,
      });
    },
  });
  return { list, pending: list.data?.pending ?? 0, generate, dismiss, accept };
}
