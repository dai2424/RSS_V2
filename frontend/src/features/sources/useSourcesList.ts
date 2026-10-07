import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";

/** URL 是筛选与分页状态来源，刷新和详情返回后仍可恢复。 */
export function useSourcesList() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "",
    category = params.get("category") || "",
    status = params.get("status") || "";
  const offset = Number(params.get("offset") || 0);
  const sources = useQuery({
    queryKey: ["sources", q, category, status, offset],
    queryFn: async () => {
      const r = await api.GET("/api/sources", {
        params: {
          query: {
            q: q || undefined,
            category_id: category || undefined,
            enabled: status ? status === "true" : undefined,
            limit: 25,
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
    offset,
    sources,
    categories,
    collect,
    setFilter,
  };
}
