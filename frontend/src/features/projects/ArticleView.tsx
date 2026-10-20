import { useState, type MouseEvent } from "react";
import type { components } from "../../api/generated";
import { CitationSheet } from "./CitationSheet";

type Preview = components["schemas"]["ArticlePreviewResponse"];
type Citation = components["schemas"]["ArticleCitationResponse"];

/**
 * 全文阅读视图：渲染服务端的受控 HTML，点引用标记就地展开证据。
 *
 * **这是整个前端唯一注入服务端 HTML 的地方**：字符串来自 `GET .../preview`，
 * 由 `domain/article_markdown.py` 先逐字符转义、再插入白名单标签生成（输入里的
 * HTML/脚本只会变成文字）。界面规范禁止注入"未清洗"的 HTML，这里的清洗发生在
 * 生成端且是可单测的纯函数；前端不再做二次解析。
 *
 * 点击用事件委托：标记在 HTML 里是 `<button data-marker="n">`，不绑定内联脚本，
 * 也就不需要为每个标记单独渲染 React 节点。
 */
export function ArticleView({ preview }: { preview: Preview }) {
  const [citation, setCitation] = useState<Citation | null>(null);

  const onClick = (event: MouseEvent<HTMLElement>) => {
    const element = event.target instanceof Element ? event.target : null;
    const marker = element
      ?.closest("[data-marker]")
      ?.getAttribute("data-marker");
    if (!marker) return;
    event.preventDefault();
    setCitation(
      preview.citations.find((item) => item.marker === marker) ?? null,
    );
  };

  return (
    <>
      <article
        className="prose article-view"
        onClick={onClick}
        dangerouslySetInnerHTML={{ __html: preview.html }}
      />
      <CitationSheet citation={citation} onClose={() => setCitation(null)} />
    </>
  );
}
