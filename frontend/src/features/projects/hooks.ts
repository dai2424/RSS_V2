import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import type { components } from "../../api/generated";

/**
 * 写作项目的数据 hooks：项目本体、资料快照、事实与核对结果。
 *
 * 状态一律来自服务端：资料准备与资料整理都是后台任务，界面按 task_status 显示
 * 排队/运行/失败，按 counts 与 gap 列表显示"读到了什么、缺什么"。
 */

/** 结束态：不再轮询。 */
const TERMINAL = new Set(["succeeded", "failed"]);

function isRunning(status: string | null | undefined) {
  return status !== null && status !== undefined && !TERMINAL.has(status);
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

export function useMaterials(projectId: string) {
  return useQuery({
    queryKey: ["materials", projectId],
    enabled: Boolean(projectId),
    refetchInterval: (query) =>
      isRunning(query.state.data?.task_status) ? 3000 : 20000,
    queryFn: async () => {
      const r = await api.GET("/api/projects/{project_id}/materials", {
        params: { path: { project_id: projectId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function useMaterialDetail(
  projectId: string,
  materialId: string | null,
) {
  return useQuery({
    queryKey: ["material", projectId, materialId],
    enabled: Boolean(projectId && materialId),
    queryFn: async () => {
      const r = await api.GET(
        "/api/projects/{project_id}/materials/{material_id}",
        {
          params: {
            path: { project_id: projectId, material_id: materialId ?? "" },
          },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function useDigest(projectId: string, runId?: string) {
  return useQuery({
    queryKey: ["digest", projectId, runId ?? "latest"],
    enabled: Boolean(projectId),
    refetchInterval: (query) =>
      isRunning(query.state.data?.task_status) ? 3000 : 20000,
    queryFn: async () => {
      const r = await api.GET("/api/projects/{project_id}/facts", {
        params: {
          path: { project_id: projectId },
          query: runId ? { run_id: runId } : {},
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function useDigestRuns(projectId: string) {
  return useQuery({
    queryKey: ["digest-runs", projectId],
    enabled: Boolean(projectId),
    queryFn: async () => {
      const r = await api.GET("/api/projects/{project_id}/digest-runs", {
        params: { path: { project_id: projectId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

/** 发起资料准备；force=true 是"重新获取资料"（来源更新或上次有资料没读到）。 */
export function usePrepareMaterials(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (force: boolean) => {
      const r = await api.POST("/api/projects/{project_id}/materials", {
        params: {
          path: { project_id: projectId },
          query: force ? { force: true } : {},
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["materials", projectId],
      });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });
}

/** 发起资料整理；force=true 是用户主动重跑（换模型/提示词后重来一遍）。 */
export function useDigestMaterials(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (force: boolean) => {
      const r = await api.POST("/api/projects/{project_id}/facts", {
        params: {
          path: { project_id: projectId },
          query: force ? { force: true } : {},
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["digest", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["digest-runs", projectId],
      });
    },
  });
}

/** 标记缺口/分歧已处理。 */
export function useResolveNote(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (noteId: string) => {
      const r = await api.POST(
        "/api/projects/{project_id}/notes/{note_id}/resolve",
        {
          params: { path: { project_id: projectId, note_id: noteId } },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["digest", projectId] });
    },
  });
}

/** 提纲版本列表与项目级当前状态（含最近一次生成任务的状态）。 */
export function useOutlines(projectId: string) {
  return useQuery({
    queryKey: ["outlines", projectId],
    enabled: Boolean(projectId),
    refetchInterval: (query) =>
      isRunning(query.state.data?.task_status) ? 3000 : 20000,
    queryFn: async () => {
      const r = await api.GET("/api/projects/{project_id}/outlines", {
        params: { path: { project_id: projectId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

/** 发起提纲生成；force=true 是用户主动重新生成（同一资料包也新起版本）。 */
export function useGenerateOutline(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (force: boolean) => {
      const r = await api.POST("/api/projects/{project_id}/outlines", {
        params: {
          path: { project_id: projectId },
          query: force ? { force: true } : {},
        },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["outlines", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });
}

/** 基于某一版保存人工稿：服务端另存新版本，不改写被改的那一版。 */
export function useSaveOutline(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      outlineId,
      body,
    }: {
      outlineId: string;
      body: components["schemas"]["OutlineSaveRequest"];
    }) => {
      const r = await api.PUT(
        "/api/projects/{project_id}/outlines/{outline_id}",
        {
          params: { path: { project_id: projectId, outline_id: outlineId } },
          body,
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["outlines", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });
}

/** 确认某个提纲版本：只有最新版本可确认，重复确认幂等。 */
export function useConfirmOutline(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (outlineId: string) => {
      const r = await api.POST(
        "/api/projects/{project_id}/outlines/{outline_id}/confirm",
        {
          params: { path: { project_id: projectId, outline_id: outlineId } },
        },
      );
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["outlines", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });
}
