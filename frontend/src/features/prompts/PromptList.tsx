import { useId } from "react";
import type { Prompt } from "./usePrompts";

const TASK_LABELS: Record<string, string> = {
  translate_message: "翻译成中文",
  enrich_message: "内容加工",
};

/**
 * 左栏提示词版本列表：按任务类型分组，每行展示版本串、名称与状态；
 * 点击切换右栏详情。
 */
export function PromptList({
  prompts,
  selectedId,
  onSelect,
}: {
  prompts: Prompt[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const titleId = useId();
  const groups = new Map<string, Prompt[]>();
  for (const prompt of prompts) {
    const list = groups.get(prompt.task_kind) ?? [];
    list.push(prompt);
    groups.set(prompt.task_kind, list);
  }
  return (
    <nav className="card provider-list-card" aria-labelledby={titleId}>
      <h2 id={titleId}>提示词版本</h2>
      {[...groups.entries()].map(([kind, items]) => (
        <div key={kind}>
          <p className="muted provider-list-group">
            {TASK_LABELS[kind] ?? kind}
          </p>
          <ul className="provider-list">
            {items.map((prompt) => (
              <li key={prompt.id}>
                <button
                  type="button"
                  className={
                    prompt.id === selectedId
                      ? "provider-list-item selected"
                      : "provider-list-item"
                  }
                  aria-current={prompt.id === selectedId || undefined}
                  onClick={() => onSelect(prompt.id)}
                >
                  <span className="provider-list-name">
                    {prompt.version_string}
                  </span>
                  <span className="muted">
                    {prompt.status === "active"
                      ? "启用中"
                      : prompt.status === "draft"
                        ? "草稿"
                        : "已归档"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
