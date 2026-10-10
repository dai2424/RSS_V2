import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import { sizeParams, usePaginationParams } from "../../lib/usePaginationParams";

/** 关键词页的数据读取：概览、词表、合并记录；筛选状态全部写在 URL 上。 */
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

  const list = useQuery({
    queryKey: ["keyword-vocabulary", q, kind, minCount, size, offset],
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/keywords", {
        params: {
          query: {
            q: q || undefined,
            kind: kind || undefined,
            min_count: minCount,
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
    size,
    offset,
    list,
    setFilter,
    setPageOffset,
    setSize,
    clear,
  };
}
