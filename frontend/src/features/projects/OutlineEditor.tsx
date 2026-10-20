import { useEffect, useMemo, useState } from "react";
import { Badge, Button } from "../../components/ui";
import type { components } from "../../api/generated";

type OutlineVersion = components["schemas"]["OutlineVersionResponse"];
type Fact = components["schemas"]["FactResponse"];

/** 表单里的一节；要点与影响分析按"一行一条"编辑，保存时再拆成数组。 */
type SectionForm = {
  heading: string;
  points: string;
  factIds: string[];
  impact: string;
};

type FormState = {
  title: string;
  angle: string;
  sections: SectionForm[];
};

/** 状态说明：缺引文的事实不能作为依据，界面上直接说明原因而不是让它可选。 */
const FACT_STATUS_LABELS: Record<string, string> = {
  supported: "有支持证据",
  conflicting: "来源冲突",
  missing: "缺料待核对",
};

function linesToArray(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function toForm(version: OutlineVersion): FormState {
  return {
    title: version.title,
    angle: version.angle,
    sections: version.sections.map((section) => ({
      heading: section.heading,
      points: section.talking_points.join("\n"),
      factIds: [...section.fact_ids],
      impact: section.impact_notes.join("\n"),
    })),
  };
}

/**
 * 提纲编辑器：结构化表单（标题、切入点、分节列表）。
 *
 * 每节勾选"依据事实"，引文由所选事实自动带出——用户不需要在引文 id 上手抄，
 * 也不会出现"勾了事实但引文漏了"的组合。保存时服务端仍会逐条核对，越界引用会被
 * 丢弃并在保存结果里计数。
 */
export function OutlineEditor({
  version,
  facts,
  saving,
  error,
  onSave,
  onCancel,
  onDirtyChange,
}: {
  version: OutlineVersion;
  facts: Fact[];
  saving: boolean;
  error?: string;
  onSave: (body: components["schemas"]["OutlineSaveRequest"]) => void;
  onCancel: () => void;
  /** 未保存状态上报给分区，用于切换版本或切换页签前拦截。 */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const baseline = useMemo(() => toForm(version), [version]);
  const [form, setForm] = useState<FormState>(baseline);
  useEffect(() => setForm(baseline), [baseline]);

  // 只有能作为依据的事实可以勾选：缺引文的事实勾了也会被服务端丢弃。
  const selectable = facts.filter((fact) => fact.status !== "missing");
  const evidenceOf = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const fact of facts) {
      map.set(
        fact.id,
        fact.evidence.map((item) => item.id),
      );
    }
    return map;
  }, [facts]);

  const dirty = JSON.stringify(form) !== JSON.stringify(baseline);
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const update = (index: number, patch: Partial<SectionForm>) =>
    setForm((current) => ({
      ...current,
      sections: current.sections.map((section, position) =>
        position === index ? { ...section, ...patch } : section,
      ),
    }));

  const toggleFact = (index: number, factId: string) =>
    setForm((current) => ({
      ...current,
      sections: current.sections.map((section, position) => {
        if (position !== index) return section;
        const selected = section.factIds.includes(factId);
        return {
          ...section,
          factIds: selected
            ? section.factIds.filter((item) => item !== factId)
            : [...section.factIds, factId],
        };
      }),
    }));

  const submit = () =>
    onSave({
      title: form.title,
      angle: form.angle,
      sections: form.sections.map((section) => ({
        heading: section.heading,
        talking_points: linesToArray(section.points),
        fact_ids: section.factIds,
        // 引文由所选事实自动带出：不单独编辑，避免"有事实没引文"的组合。
        evidence_ids: section.factIds.flatMap(
          (factId) => evidenceOf.get(factId) ?? [],
        ),
        impact_notes: linesToArray(section.impact),
      })),
    });

  return (
    <div className="stack">
      <p className="muted">
        {`保存会另存为新的草稿版本（第 ${version.version_number + 1} 版），当前这一版保持不变；确认仍只对最新版本生效。`}
      </p>
      <div className="form-field">
        <label htmlFor="outline-title">文章标题</label>
        <input
          id="outline-title"
          value={form.title}
          onChange={(event) =>
            setForm((current) => ({ ...current, title: event.target.value }))
          }
        />
      </div>
      <div className="form-field">
        <label htmlFor="outline-angle">切入点</label>
        <textarea
          id="outline-angle"
          rows={3}
          value={form.angle}
          placeholder="这篇从什么角度讲、为什么值得普通读者读"
          onChange={(event) =>
            setForm((current) => ({ ...current, angle: event.target.value }))
          }
        />
      </div>
      {form.sections.map((section, index) => (
        <fieldset key={index} className="outline-section">
          <legend>第 {index + 1} 节</legend>
          <div className="form-field">
            <label htmlFor={`outline-heading-${index}`}>小标题</label>
            <input
              id={`outline-heading-${index}`}
              value={section.heading}
              onChange={(event) =>
                update(index, { heading: event.target.value })
              }
            />
          </div>
          <div className="form-field">
            <label htmlFor={`outline-points-${index}`}>要点（一行一条）</label>
            <textarea
              id={`outline-points-${index}`}
              rows={4}
              value={section.points}
              onChange={(event) =>
                update(index, { points: event.target.value })
              }
            />
          </div>
          <div className="form-field">
            <label htmlFor={`outline-impact-${index}`}>
              行业影响分析（一行一条，可留空）
            </label>
            <textarea
              id={`outline-impact-${index}`}
              rows={2}
              value={section.impact}
              onChange={(event) =>
                update(index, { impact: event.target.value })
              }
            />
          </div>
          <div className="form-field">
            <span className="muted">
              依据事实（勾选后引文自动带出；缺引文的事实不能作为依据）
            </span>
            {selectable.length === 0 ? (
              <p className="muted">
                这次资料包里没有可用事实，这一节只能作为结构占位。
              </p>
            ) : (
              <ul className="outline-facts">
                {selectable.map((fact) => (
                  <li key={fact.id} className="outline-fact">
                    <label>
                      <input
                        type="checkbox"
                        checked={section.factIds.includes(fact.id)}
                        onChange={() => toggleFact(index, fact.id)}
                      />
                      <span>{fact.statement}</span>
                    </label>
                    <Badge
                      tone={fact.status === "supported" ? "success" : "warning"}
                    >
                      {FACT_STATUS_LABELS[fact.status] ?? fact.status}
                    </Badge>
                    <span className="muted">{fact.evidence.length} 条引文</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <Button
            className="secondary danger-outline"
            disabled={form.sections.length <= 2}
            onClick={() =>
              setForm((current) => ({
                ...current,
                sections: current.sections.filter(
                  (_item, position) => position !== index,
                ),
              }))
            }
          >
            删除本节
          </Button>
        </fieldset>
      ))}
      <div className="toolbar">
        <Button
          className="secondary"
          onClick={() =>
            setForm((current) => ({
              ...current,
              sections: [
                ...current.sections,
                { heading: "", points: "", factIds: [], impact: "" },
              ],
            }))
          }
        >
          添加一节
        </Button>
        <Button disabled={saving || !dirty} onClick={submit}>
          {saving ? "正在保存…" : "保存为新版本"}
        </Button>
        <Button className="secondary" disabled={saving} onClick={onCancel}>
          取消编辑
        </Button>
        {dirty && <span className="muted">有未保存的修改</span>}
      </div>
      {error && (
        <div className="field-error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
