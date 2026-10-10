import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import { usePaginationParams } from "../../lib/usePaginationParams";

/** URL 是筛选与分页状态来源，刷新和详情返回后仍可恢复。 */
export function useSourcesList() {
  const { params, setParams, size, offset, setPageOffset, setSize } =
    usePaginationParams();
  const q = params.get("q") || "",
    category = params.get("category") || "",
    status = params.get("status") || "";
  const sources = useQuery({
    queryKey: ["sources", q, category, status, size, offset],
    // 翻页时保留上一页内容，避免表格整页闪烁；配合分页条禁用挡住连点。
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/sources", {
        params: {
          query: {
            q: q || undefined,
            category_id: category || undefined,
            enabled: status ? status === "true" : undefined,
            limit: size,
            offset,
          },
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async () => {
      const r = await api.GET("/api/categories");
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const collect = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/collection/runs", {
        body: { all_enabled: true, source_ids: [] },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    next.delete("offset");
    setParams(next, { replace: true });
  };
  return {
    params,
    setParams,
    q,
    category,
    status,
    size,
    offset,
    setPageOffset,
    setSize,
    sources,
    categories,
    collect,
    setFilter,
  };
}
