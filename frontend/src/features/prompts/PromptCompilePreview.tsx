import type { CompileResult } from "./usePrompts";

/**
 * 编译结果：问题清单与渲染预览。
 * 就地显示在表单里而不是浮层，与 Web 界面规范"编译错误留在页面内"一致。
 */
export function PromptCompilePreview({ result }: { result: CompileResult }) {
  return (
    <>
      <div className={result.ok ? "alert" : "alert alert-error"} role="status">
        {result.ok ? "编译通过" : "编译未通过"}
        <ul className="issue-list">
          {result.errors.map((item) => (
            <li key={`e-${item.field}-${item.message}`}>
              错误：{item.message}
            </li>
          ))}
          {result.warnings.map((item) => (
            <li key={`w-${item.field}-${item.message}`}>
              提示：{item.message}
            </li>
          ))}
        </ul>
      </div>
      {result.ok && (
        <div className="prompt-preview">
          <h3>渲染预览（系统提示）</h3>
          <pre className="prompt-text">{result.system || "（空）"}</pre>
          <h3>渲染预览（用户提示）</h3>
          <pre className="prompt-text">{result.user}</pre>
        </div>
      )}
    </>
  );
}
