import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, useCallback } from "react";
import { api, requireResponse } from "../../api/client";
import { Card, EmptyState, ErrorState, PageHeader } from "../../components/ui";
import { UnsavedChanges } from "../../components/UnsavedChanges";
import { ProviderForm } from "./ProviderForm";
import { ProviderCard } from "./ProviderCard";
import type { DirtyChange } from "./types";

/** 读取 Provider 列表并聚合局部表单状态；不接收或显示原始 Key。 */
export function ProvidersPage() {
  const [dirtyForms, setDirtyForms] = useState(new Set<string>());
  const onDirtyChange = useCallback<DirtyChange>((id, dirty) => {
    setDirtyForms((previous) => {
      if (previous.has(id) === dirty) return previous;
      const next = new Set(previous);
      if (dirty) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  const queryClient = useQueryClient();
  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: ["providers"] });
  const providers = useQuery({
    queryKey: ["providers"],
    refetchInterval: 5000,
    queryFn: async () => {
      const r = await api.GET("/api/llm/providers");
      return requireResponse(r.response, r.data, r.error);
    },
  });
  return (
    <div className="page">
      <PageHeader
        title="模型配置"
        description="API Key 只保存引用；数值越小，优先级越高。"
      />
      <div className="stack">
        <Card className="card-pad">
          <h2>添加 Provider</h2>
          <ProviderForm onSaved={refresh} onDirtyChange={onDirtyChange} />
        </Card>
        {providers.isLoading && <div className="loading">正在加载配置…</div>}
        {providers.isError && (
          <ErrorState
            message={providers.error.message}
            onRetry={() => void providers.refetch()}
          />
        )}
        {providers.data?.length === 0 && (
          <EmptyState
            title="暂无 Provider"
            description="添加兼容服务，配置 Key 引用后即可创建翻译任务。"
          />
        )}
        {providers.data?.map((provider) => (
          <ProviderCard
            key={provider.id}
            provider={provider}
            onSaved={refresh}
            onDirtyChange={onDirtyChange}
          />
        ))}
      </div>
      <UnsavedChanges dirty={dirtyForms.size > 0} />
    </div>
  );
}
