import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";

export const PAGE_SIZE = 25;

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
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "";
  const kind = params.get("kind") || "";
  const minCount = Number(params.get("min") || 2);
  const offset = Number(params.get("offset") || 0);

  const list = useQuery({
    queryKey: ["keyword-vocabulary", q, kind, minCount, offset],
    queryFn: async () => {
      const r = await api.GET("/api/keywords", {
        params: {
          query: {
            q: q || undefined,
            kind: kind || undefined,
            min_count: minCount,
            limit: PAGE_SIZE,
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
  const page = (next: number) => {
    const target = new URLSearchParams(params);
    target.set("offset", String(Math.max(0, next)));
    setParams(target);
  };
  const clear = () => setParams({}, { replace: true });

  return { params, q, kind, minCount, offset, list, setFilter, page, clear };
}
