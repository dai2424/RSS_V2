import { useState } from "react";
import { Card, ErrorState } from "../../components/ui";
import { PromptDetail } from "./PromptDetail";
import { PromptList } from "./PromptList";
import { usePrompts } from "./usePrompts";

/** 左栏版本列表 + 右栏详情，与模型配置页同一套布局。 */
export function PromptsPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const prompts = usePrompts();
  const active =
    prompts.data?.find((item) => item.id === selected) ??
    prompts.data?.[0] ??
    null;
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>提示词</h1>
          <p className="muted">
            按任务类型管理提示词：编辑生成新版本，启用后生效；版本不可修改，历史版本可归档但不会被删除。
          </p>
        </div>
      </div>
      {prompts.isLoading && <div className="loading">正在加载提示词…</div>}
      {prompts.isError && (
        <ErrorState
          message={prompts.error.message}
          onRetry={() => void prompts.refetch()}
        />
      )}
      {!!prompts.data?.length && (
        <div className="provider-layout">
          <PromptList
            prompts={prompts.data}
            selectedId={active?.id ?? null}
            onSelect={setSelected}
          />
          {active ? (
            <PromptDetail key={active.id} prompt={active} />
          ) : (
            <Card className="card-pad">
              <p className="muted">从左侧选择一个提示词版本。</p>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
