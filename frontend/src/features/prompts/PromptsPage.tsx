import { useState } from "react";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/ui";
import { PromptDetail } from "./PromptDetail";
import { PromptEditor } from "./PromptEditor";
import { PromptList } from "./PromptList";
import { usePrompts } from "./usePrompts";

/**
 * 左栏版本列表 + 右栏详情，与模型配置页同一套布局。
 * 新建和编辑都用页面内的表单（提示词是长正文，不用弹层），保存前后都能编译预览。
 */
export function PromptsPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const prompts = usePrompts();
  const list = prompts.data;
  const active =
    list?.find((item) => item.id === selected) ?? list?.[0] ?? null;
  return (
    <div className="page page-wide">
      <PageHeader
        title="提示词"
        description="按任务类型管理提示词：启用后生效，未被引用过的版本可以就地编辑或删除，其余只能另存新版本或归档。"
        action={
          <Button onClick={() => setCreating(true)} disabled={creating}>
            新建提示词
          </Button>
        }
      />
      {prompts.isLoading && <div className="loading">正在加载提示词…</div>}
      {prompts.isError && (
        <ErrorState
          message={prompts.error.message}
          onRetry={() => void prompts.refetch()}
        />
      )}
      {creating && (
        <PromptEditor
          mode="create"
          onDone={() => setCreating(false)}
          onCreated={(prompt) => setSelected(prompt.id)}
        />
      )}
      {list?.length === 0 && !creating && (
        <EmptyState
          title="还没有提示词"
          description="新建一个提示词版本并启用，翻译和内容加工才会使用它。"
        />
      )}
      {!!list?.length && (
        <div className="provider-layout">
          <PromptList
            prompts={list}
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
