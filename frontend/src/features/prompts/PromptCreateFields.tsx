/** 新建提示词才有的两块字段：任务类型与业务键。 */
export function PromptCreateFields({
  taskKind,
  promptKey,
  options,
  onTaskKindChange,
  onPromptKeyChange,
}: {
  taskKind: string;
  promptKey: string;
  options: { task_kind: string; label: string }[];
  onTaskKindChange: (value: string) => void;
  onPromptKeyChange: (value: string) => void;
}) {
  return (
    <>
      <div className="form-field">
        <label htmlFor="prompt-task-kind">任务类型</label>
        <select
          id="prompt-task-kind"
          value={taskKind}
          onChange={(e) => onTaskKindChange(e.target.value)}
        >
          {options.map((item) => (
            <option key={item.task_kind} value={item.task_kind}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
      <div className="form-field">
        <label htmlFor="prompt-key">提示词标识</label>
        <input
          id="prompt-key"
          value={promptKey}
          placeholder="production-brief"
          onChange={(e) => onPromptKeyChange(e.target.value)}
        />
        <p className="muted">
          用小写字母、数字和短横线；版本串「标识-v版本号」会写进调用审计与任务记录，创建后不能再改。
        </p>
      </div>
    </>
  );
}
