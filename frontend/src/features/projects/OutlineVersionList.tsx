import { Badge, Button } from "../../components/ui";
import { formatTime } from "../../lib/display";
import type { components } from "../../api/generated";

type OutlineVersion = components["schemas"]["OutlineVersionResponse"];

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  confirmed: "已确认",
  superseded: "已被取代",
};

const STATUS_TONES: Record<string, "success" | "warning" | "neutral"> = {
  draft: "warning",
  confirmed: "success",
  superseded: "neutral",
};

const SOURCE_LABELS: Record<string, string> = {
  model: "模型生成",
  human: "人工保存",
};

/** 丢弃计数的中文说明：每一项都要能读懂"丢了什么、为什么"。 */
function counterNotes(version: OutlineVersion): string[] {
  const counters = version.counters;
  const notes: string[] = [];
  if (counters.out_of_range > 0)
    notes.push(`越界引用 ${counters.out_of_range} 条（已丢弃）`);
  if (counters.unsupported > 0)
    notes.push(`引用了缺引文的事实 ${counters.unsupported} 条（已丢弃）`);
  if (counters.dropped_sections > 0)
    notes.push(
      `无小标题或超出上限的小节 ${counters.dropped_sections} 个（已丢弃）`,
    );
  if (counters.dropped_points > 0)
    notes.push(`超出每节上限的要点 ${counters.dropped_points} 条（已丢弃）`);
  if (counters.without_facts > 0)
    notes.push(`${counters.without_facts} 个小节没有任何事实依据`);
  return notes;
}

/**
 * 提纲版本列表：徽标（版本 / 状态 / 来源）、节数、丢弃计数与三个操作。
 *
 * 与详情卡拆开：列表只负责"选哪一版、能对它做什么"，改动/确认的副作用仍由分区
 * 组件持有，避免列表项各自记一份状态。
 */
export function OutlineVersionList({
  versions,
  confirming,
  onSelect,
  onEdit,
  onConfirm,
}: {
  versions: OutlineVersion[];
  confirming: boolean;
  onSelect: (versionId: string) => void;
  onEdit: (versionId: string) => void;
  onConfirm: (version: OutlineVersion) => void;
}) {
  return (
    <ul className="related-list">
      {versions.map((version) => (
        <li key={version.id} className="related-item">
          <div className="related-item-shared">
            <Badge>第 {version.version_number} 版</Badge>
            <Badge tone={STATUS_TONES[version.status] ?? "neutral"}>
              {STATUS_LABELS[version.status] ?? version.status}
            </Badge>
            <Badge tone="neutral">
              {SOURCE_LABELS[version.source] ?? version.source}
            </Badge>
            <span className="muted">
              {formatTime(version.confirmed_at ?? version.created_at)}
              {version.is_latest && " · 最新"}
            </span>
          </div>
          <strong>{version.title || "（无标题）"}</strong>
          <span className="muted">
            {version.sections.length} 节
            {version.based_on_version && " · 基于旧版本修改"}
          </span>
          {counterNotes(version).length > 0 && (
            <span className="field-error">
              {counterNotes(version).join(" · ")}
            </span>
          )}
          <div className="toolbar">
            <Button className="secondary" onClick={() => onSelect(version.id)}>
              查看这一版
            </Button>
            <Button className="secondary" onClick={() => onEdit(version.id)}>
              编辑这一版
            </Button>
            {version.is_latest && version.status !== "confirmed" && (
              <Button disabled={confirming} onClick={() => onConfirm(version)}>
                确认这一版
              </Button>
            )}
            {version.status === "confirmed" && (
              <span className="muted">当前确认版本</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
