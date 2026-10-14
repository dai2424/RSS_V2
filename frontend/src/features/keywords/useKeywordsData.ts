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
  // 口径默认"热词"：窗口与门槛由服务端按配置折算，界面只负责切换与显示。
  const scope = params.get("scope") === "all" ? "all" : "hot";
  const minParam = params.get("min") || "";
  const minCount = minParam ? Number(minParam) : null;
  const pinned = params.get("pinned") === "1";

  const list = useQuery({
    queryKey: [
      "keyword-vocabulary",
      q,
      kind,
      scope,
      minParam,
      pinned,
      size,
      offset,
    ],
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/keywords", {
        params: {
          query: {
            q: q || undefined,
            kind: kind || undefined,
            scope,
            min_count: minCount ?? undefined,
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
    scope,
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

/** 合并建议：待确认列表、排队批次与处置。批次由独立 worker 执行，因此这里轮询进度。 */
export function useKeywordSuggestions() {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["keyword-suggestions"],
    // 有批次在排队或运行时定时刷新，让结果自己出现；闲着就不轮询。
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.queued + data.running > 0 ? 3000 : false;
    },
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
  const enqueue = useMutation({
    // count 是批数：1 批用于试探，整表扫完就一次多排几批，由 worker 慢慢跑。
    mutationFn: async (count: number) => {
      const r = await api.POST("/api/keywords/suggestions", {
        body: { count },
      });
      const data = requireResponse(r.response, r.data, r.error);
      await refresh();
      return data;
    },
    onSuccess: (data) => {
      // 没排出新批次有两种原因：还有批次在跑，或者词表已经看完了。
      const running = data.queued + data.running;
      showToast({
        type: "info",
        content:
          data.enqueued > 0
            ? `已排入 ${data.enqueued} 个批次、共 ${data.queued_words} 个词，可在任务页看进度。`
            : running > 0
              ? `已有 ${running} 个批次在排队或运行，等它们跑完。`
              : "词表都看过了，没有需要排的批次。",
      });
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
  const state = list.data;
  const total = state?.total ?? 0;
  const scanned = state?.scanned ?? 0;
  return {
    list,
    pending: state?.pending ?? 0,
    queued: (state?.queued ?? 0) + (state?.running ?? 0),
    failed: state?.failed ?? 0,
    total,
    // 从没交给模型看过的词：扫描是滚动的，这个数只表示"还没排上的新词"。
    fresh: Math.max(0, total - scanned),
    lastScannedAt: state?.last_scanned_at ?? null,
    enqueue,
    dismiss,
    accept,
  };
}

/** 常驻建议：候选由服务端按跨周出现算出，模型只挑选，采纳后才常驻。 */
export function usePinSuggestions() {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["keyword-pin-suggestions"],
    queryFn: async () => {
      const r = await api.GET("/api/keywords/pin-suggestions", {
        params: { query: { status: "pending", limit: 50 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["keyword-pin-suggestions"],
    });
    await queryClient.invalidateQueries({ queryKey: ["keyword-vocabulary"] });
    await queryClient.invalidateQueries({ queryKey: ["keyword-overview"] });
  };
  const suggest = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/keywords/pin-suggestions", { body: { dry_run: false } });
      const data = requireResponse(r.response, r.data, r.error);
      await refresh();
      return data;
    },
    onSuccess: (data) => {
      showToast({
        type: data.picks > 0 ? "success" : "info",
        content:
          data.candidates === 0
            ? "样本还不够：候选要求最近四周里至少三周出现。"
            : data.picks > 0
              ? `从 ${data.candidates} 个跨周候选里挑出 ${data.picks} 个，确认后才会常驻。`
              : `看了 ${data.candidates} 个跨周候选，没有值得常驻的新词。`,
      });
    },
    onError: (error: Error) => {
      showToast({ type: "error", content: error.message });
    },
  });
  const resolve = useMutation({
    mutationFn: async (input: { key: string; accepted: boolean }) => {
      const r = input.accepted
        ? await api.POST("/api/keywords/pin-suggestions/{key}/accept", {
            params: { path: { key: input.key } },
          })
        : await api.POST("/api/keywords/pin-suggestions/{key}/dismiss", {
            params: { path: { key: input.key } },
          });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async (_data, input) => {
      await refresh();
      showToast({
        type: "success",
        content: input.accepted
          ? "已常驻，注入提示词时必然出现。"
          : "已忽略这条常驻建议。",
      });
    },
    onError: (error: Error) => {
      showToast({ type: "error", content: error.message });
    },
  });
  return { list, suggest, resolve, pending: list.data?.pending ?? 0 };
}
