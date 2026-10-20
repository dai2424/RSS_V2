import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "../../components/ui/sheet";
import { Badge } from "../../components/ui";
import { safeLink } from "../../lib/display";
import type { components } from "../../api/generated";

type Citation = components["schemas"]["ArticleCitationResponse"];

/** 生成时的核对结论：与事实面板同一套文案，避免各页自行解释状态码。 */
const STATUS_LABELS: Record<string, string> = {
  supported: "有支持证据",
  conflicting: "来源冲突",
  missing: "缺料待核对",
};

const KIND_LABELS: Record<string, string> = {
  fact: "客观事实",
  analysis: "分析",
  speculation: "推测",
};

const STATUS_TONES: Record<string, "success" | "warning" | "danger"> = {
  supported: "success",
  conflicting: "warning",
  missing: "danger",
};

/**
 * 引用证据面板：点正文里的 [^n] 就地展开，不离开阅读位置。
 *
 * 数据来自预览响应里的快照，不再发请求；来源链接沿用 `safeLink`（只放行 http/https）。
 */
export function CitationSheet({
  citation,
  onClose,
}: {
  citation: Citation | null;
  onClose: () => void;
}) {
  return (
    <Sheet open={citation !== null} onOpenChange={(next) => !next && onClose()}>
      <SheetContent side="bottom" className="sm:mx-auto sm:max-w-2xl">
        <SheetTitle>
          {citation ? `引用 [^${citation.marker}]` : "引用"}
        </SheetTitle>
        <SheetDescription>
          这是正文标记对应的已核对事实与引文原文；引文在资料整理阶段已核对过出处。
        </SheetDescription>
        {citation && (
          <div className="stack">
            <div className="badge-row">
              <Badge tone="neutral">
                {KIND_LABELS[citation.fact_kind] ?? citation.fact_kind}
              </Badge>
              <Badge tone={STATUS_TONES[citation.fact_status] ?? "neutral"}>
                {STATUS_LABELS[citation.fact_status] ?? citation.fact_status}
              </Badge>
            </div>
            <p>{citation.statement}</p>
            <div className="form-field">
              <span className="muted">引文原文</span>
              {citation.quotes.length === 0 ? (
                <p className="field-error">这条引用没有可用引文。</p>
              ) : (
                <ul className="related-list">
                  {citation.quotes.map((quote) => {
                    const href = safeLink(quote.material_url);
                    return (
                      <li key={quote.text} className="related-item">
                        <blockquote className="citation-quote">
                          {quote.text}
                        </blockquote>
                        <span className="muted">
                          来源：
                          {href ? (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {quote.material_title || quote.material_url}
                            </a>
                          ) : (
                            quote.material_title || quote.material_url
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
