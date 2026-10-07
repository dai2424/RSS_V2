import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, requireResponse } from "../../api/client";

/** 来源详情轮询与测试、启停、采集用例；动作后刷新相关缓存。 */
export function useSourceDetail() {
  const { sourceId = "" } = useParams();
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["source", sourceId] });
    void queryClient.invalidateQueries({ queryKey: ["sources"] });
  };
  const source = useQuery({
    queryKey: ["source", sourceId],
    refetchInterval: 3000,
    queryFn: async () => {
      const result = await api.GET("/api/sources/{source_id}", {
        params: { path: { source_id: sourceId } },
      });
      return requireResponse(result.response, result.data, result.error);
    },
  });
  const testSource = useMutation({
    mutationFn: async () => {
      const result = await api.POST("/api/sources/{source_id}/test", {
        params: { path: { source_id: sourceId } },
      });
      return requireResponse(result.response, result.data, result.error);
    },
    onSettled: refresh,
  });
  const toggleSource = useMutation({
    mutationFn: async (enabled: boolean) => {
      const result = await api.PATCH("/api/sources/{source_id}", {
        params: { path: { source_id: sourceId } },
        body: { enabled },
      });
      return requireResponse(result.response, result.data, result.error);
    },
    onSuccess: refresh,
  });
  const collect = useMutation({
    mutationFn: async () => {
      const result = await api.POST("/api/collection/runs", {
        body: { source_ids: [sourceId], all_enabled: false },
      });
      return requireResponse(result.response, result.data, result.error);
    },
    onSuccess: refresh,
  });
  return { source, testSource, toggleSource, collect };
}
