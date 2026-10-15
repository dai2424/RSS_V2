import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";

/**
 * 选题发现的数据 hooks：批次列表、批次详情、写作项目，以及范围选项与两个动作。
 *
 * 界面状态一律来自服务端：批次的执行结果看 `status`，是否在跑看 `task_status`
 * （重试期间批次仍保留上次失败原因，任务才是"现在在不在跑"）。
 */

/** 结束态：这两种状态不再轮询，其余继续刷。 */
const TERMINAL = new Set(["succeeded", "failed"]);

export function isBatchRunning(batch: {
  status: string;
  task_status: string | null;
}) {
  return (
    !TERMINAL.has(batch.status) ||
    batch.task_status === "queued" ||
    batch.task_status === "running"
  );
}

export function useDiscoveries({
  limit,
  offset,
}: {
  limit: number;
  offset: number;
}) {
  return useQuery({
    queryKey: ["discoveries", limit, offset],
    // 有批次在跑时轮询快一点；全部结束后 15 秒兜底刷新，避免长时间空转。
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some(isBatchRunning) ? 4000 : 15000;
    },
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const r = await api.GET("/api/discoveries", {
        params: { query: { limit, offset } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function useDiscoveryDetail(batchId: string) {
  return useQuery({
    queryKey: ["discovery", batchId],
    enabled: Boolean(batchId),
    refetchInterval: (query) =>
      query.state.data && !isBatchRunning(query.state.data.batch)
        ? 15000
        : 3000,
    queryFn: async () => {
      const r = await api.GET("/api/discoveries/{batch_id}", {
        params: { path: { batch_id: batchId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function useProjectDetail(projectId: string) {
  return useQuery({
    queryKey: ["project", projectId],
    enabled: Boolean(projectId),
    queryFn: async () => {
      const r = await api.GET("/api/projects/{project_id}", {
        params: { path: { project_id: projectId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

/** 范围选项：分类与来源都要用来筛选，来源必须属于所选分类。 */
export function useScopeOptions() {
  const categories = useQuery({
    queryKey: ["category-options"],
    queryFn: async () => {
      const r = await api.GET("/api/categories");
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const sources = useQuery({
    queryKey: ["source-options"],
    queryFn: async () => {
      const r = await api.GET("/api/sources", {
        params: { query: { limit: 100 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  return {
    categories: categories.data ?? [],
    sources: sources.data?.items ?? [],
    isLoading: categories.isLoading || sources.isLoading,
  };
}

/** 发起发现：服务端相同范围与输入会复用既有批次，因此重复点击不会重复付费。 */
export function useCreateDiscovery(
  onDone: (batchId: string, reused: boolean) => void,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (scope: {
      window_start: number;
      window_end: number;
      category_id?: string;
      source_id?: string;
    }) => {
      const r = await api.POST("/api/discoveries", { body: scope });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (batch) => {
      void queryClient.invalidateQueries({ queryKey: ["discoveries"] });
      onDone(batch.id, batch.reused);
    },
  });
}

/** 选题：建立写作项目；同一候选重复选择返回既有项目。 */
export function useSelectCandidate(onDone: (projectId: string) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (target: { batchId: string; candidateId: string }) => {
      const r = await api.POST(
        "/api/discoveries/{batch_id}/candidates/{candidate_id}/select",
        {
          params: {
            path: {
              batch_id: target.batchId,
              candidate_id: target.candidateId,
            },
          },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["discovery"] });
      onDone(project.id);
    },
  });
}

/** 删除批次：候选与自动线索一并清理，人工线索不受影响。 */
export function useDeleteDiscovery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (batchId: string) => {
      const r = await api.DELETE("/api/discoveries/{batch_id}", {
        params: { path: { batch_id: batchId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["discoveries"] });
    },
  });
}
