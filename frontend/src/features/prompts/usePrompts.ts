import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, requireNoContent, requireResponse } from "../../api/client";
import type { components } from "../../api/generated";

export type Prompt = components["schemas"]["PromptResponse"];
export type PromptSpec = components["schemas"]["TaskSettingResponse"] & {
  variables?: string[];
};
export type CompileResult = components["schemas"]["PromptCompileResponse"];
export type TestResult = components["schemas"]["PromptTestResponse"];
export type PromptUsage = components["schemas"]["PromptUsageResponse"];

type CompileBody = components["schemas"]["PromptCompileRequest"];
type CreateBody = components["schemas"]["PromptCreateRequest"];
type UpdateBody = components["schemas"]["PromptUpdateRequest"];
type TestBody = components["schemas"]["PromptTestRequest"];

/** 任务规格：占位符与输出字段要求的唯一来源，界面据此提示用户。 */
export function useTaskSpecs() {
  return useQuery({
    queryKey: ["prompt-specs"],
    staleTime: Infinity,
    queryFn: async () => {
      const r = await api.GET("/api/llm/prompts/specs");
      return requireResponse(r.response, r.data, r.error) as unknown as {
        task_kind: string;
        label: string;
        variables: string[];
        required_variables: string[];
        output_fields: string[];
      }[];
    },
  });
}

export function usePrompts(taskKind?: string) {
  return useQuery({
    queryKey: ["prompts", taskKind ?? "all"],
    queryFn: async () => {
      const r = await api.GET("/api/llm/prompts", {
        params: { query: taskKind ? { task_kind: taskKind } : {} },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

/**
 * 某个版本的使用情况：决定能否就地编辑或删除。
 * 判定口径在后端仓储层（审计、结果、任务、任务分配四类引用），界面不自行推断。
 */
export function usePromptUsage(promptId: string) {
  return useQuery({
    queryKey: ["prompt-usage", promptId],
    queryFn: async () => {
      const r = await api.GET("/api/llm/prompts/{prompt_id}/usage", {
        params: { path: { prompt_id: promptId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
}

export function usePromptActions(taskKind?: string) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["prompts"] });
  };
  const refreshUsage = (promptId: string) => {
    refresh();
    void client.invalidateQueries({ queryKey: ["prompt-usage", promptId] });
  };
  const create = useMutation({
    mutationFn: async (body: CreateBody) => {
      const r = await api.POST("/api/llm/prompts", { body });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: refresh,
  });
  const update = useMutation({
    mutationFn: async ({
      promptId,
      body,
    }: {
      promptId: string;
      body: UpdateBody;
    }) => {
      const r = await api.PATCH("/api/llm/prompts/{prompt_id}", {
        params: { path: { prompt_id: promptId } },
        body,
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (prompt) => refreshUsage(prompt.id),
  });
  const remove = useMutation({
    mutationFn: async (promptId: string) => {
      const r = await api.DELETE("/api/llm/prompts/{prompt_id}", {
        params: { path: { prompt_id: promptId } },
      });
      requireNoContent(r.response, r.error);
      return promptId;
    },
    onSuccess: (promptId) => {
      client.removeQueries({ queryKey: ["prompt-usage", promptId] });
      refresh();
    },
  });
  const activate = useMutation({
    mutationFn: async (promptId: string) => {
      const r = await api.POST("/api/llm/prompts/{prompt_id}/activate", {
        params: { path: { prompt_id: promptId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: refresh,
  });
  const archive = useMutation({
    mutationFn: async (promptId: string) => {
      const r = await api.POST("/api/llm/prompts/{prompt_id}/archive", {
        params: { path: { prompt_id: promptId } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: refresh,
  });
  const compile = useMutation({
    mutationFn: async (body: CompileBody) => {
      const r = await api.POST("/api/llm/prompts/compile", { body });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const test = useMutation({
    mutationFn: async ({
      promptId,
      body,
    }: {
      promptId: string;
      body: TestBody;
    }) => {
      const r = await api.POST("/api/llm/prompts/{prompt_id}/test", {
        params: { path: { prompt_id: promptId } },
        body,
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["llm-calls"] });
    },
  });
  return { create, update, remove, activate, archive, compile, test, taskKind };
}
