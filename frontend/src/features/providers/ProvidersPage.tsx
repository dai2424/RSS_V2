import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, requireResponse } from "../../api/client";
import {
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { UnsavedChanges } from "../../components/UnsavedChanges";
import { ProviderCreateDialog } from "./ProviderCreateDialog";
import { ProviderDetail } from "./ProviderDetail";
import { ProviderList } from "./ProviderList";
import type { DirtyChange, Provider } from "./types";

/**
 * 模型配置主页面：左列表选择供应商，右详情编辑连接、模型与 API Key。
 * 页面只负责选择状态与数据获取，具体编辑在各子组件内完成。
 */
export function ProvidersPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
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
  const list: Provider[] = providers.data ?? [];
  // 新建供应商是显式选择：等它出现在列表前不覆盖，避免轮询数据落后导致跳回第一个。
  const pendingSelection = useRef<string | null>(null);
  const selectProvider = useCallback((id: string) => {
    pendingSelection.current = id;
    setSelectedId(id);
  }, []);
  useEffect(() => {
    if (list.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (
      pendingSelection.current &&
      list.some((item) => item.id === pendingSelection.current)
    ) {
      pendingSelection.current = null;
    }
    if (pendingSelection.current) return;
    if (!selectedId || !list.some((item) => item.id === selectedId)) {
      setSelectedId(list[0].id);
    }
  }, [list, selectedId]);
  const selected = useMemo(
    () => list.find((item) => item.id === selectedId) ?? null,
    [list, selectedId],
  );
  return (
    <div className="page page-wide">
      <PageHeader
        title="模型配置"
        description="管理兼容模型服务：连接、模型列表与 API Key；数值越小，优先级越高。"
        action={
          <>
            <Button
              className="secondary"
              onClick={refresh}
              disabled={providers.isFetching}
            >
              刷新
            </Button>
            <ProviderCreateDialog
              onSaved={refresh}
              onCreated={selectProvider}
              onDirtyChange={onDirtyChange}
            />
          </>
        }
      />
      {providers.isLoading && <div className="loading">正在加载配置…</div>}
      {providers.isError && (
        <ErrorState
          message={providers.error.message}
          onRetry={() => void providers.refetch()}
        />
      )}
      {providers.data && list.length === 0 && (
        <EmptyState
          title="暂无 Provider"
          description="添加兼容服务并配置模型与 API Key 后，即可创建翻译任务。"
        />
      )}
      {providers.data && list.length > 0 && (
        <div className="provider-layout">
          <ProviderList
            providers={list}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
          />
          {selected && (
            <ProviderDetail
              provider={selected}
              onSaved={refresh}
              onDirtyChange={onDirtyChange}
            />
          )}
        </div>
      )}
      <UnsavedChanges dirty={dirtyForms.size > 0} />
    </div>
  );
}
