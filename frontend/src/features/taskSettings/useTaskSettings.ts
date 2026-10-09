import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import type { components } from "../../api/generated";

export type TaskSetting = components["schemas"]["TaskSettingResponse"];
type UpdateBody = components["schemas"]["TaskSettingsUpdateRequest"];

/** 任务分配：某来源或分类要跑哪些任务、用哪条提示词。 */
export function useTaskSettings(scope: "source" | "category", scopeId: string) {
  const client = useQueryClient();
  const key = ["task-settings", scope, scopeId];
  const query = useQuery({
    queryKey: key,
    enabled: Boolean(scopeId),
    queryFn: async () => {
      const r = await api.GET("/api/task-settings/{scope}/{scope_id}", {
        params: { path: { scope, scope_id: scopeId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const save = useMutation({
    mutationFn: async (body: UpdateBody) => {
      const r = await api.PUT("/api/task-settings/{scope}/{scope_id}", {
        params: { path: { scope, scope_id: scopeId } },
        body,
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key });
    },
  });
  return { query, save };
}
