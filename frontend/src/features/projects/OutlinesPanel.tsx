import { useEffect, useRef, useState } from "react";
import { TaskStatusBanner } from "../../components/TaskStatus";
import { showToast } from "../../components/Toast";
import { UnsavedChanges, UnsavedDialog } from "../../components/UnsavedChanges";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
} from "../../components/ui";
import { formatTime } from "../../lib/display";
import type { components } from "../../api/generated";
import { OutlineEditor } from "./OutlineEditor";
import {
  useConfirmOutline,
  useDigest,
  useGenerateOutline,
  useOutlines,
  useSaveOutline,
} from "./hooks";

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
 * 提纲分区：生成、查看版本、人工修改保存与确认。
 *
 * 阻塞缺口在这里只做显著标记、不拦确认（是否拒绝生成全文留给 P5）；确认只对最新
 * 版本生效，确认后编辑会另存新版本并在界面上提示"已有更新版本尚未确认"。
 */
export function OutlinesPanel({
  projectId,
  onDirtyChange,
}: {
  projectId: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const outlines = useOutlines(projectId);
  const generate = useGenerateOutline(projectId);
  const save = useSaveOutline(projectId);
  const confirm = useConfirmOutline(projectId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const versions = outlines.data?.items ?? [];
  const selected =
    versions.find((item) => item.id === selectedId) ?? versions[0];
  const digest = useDigest(projectId, selected?.digest_run_id ?? undefined);

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  /** 有未保存修改时先确认再执行动作，避免静默丢掉编辑内容。 */
  const guard = (action: () => void) => {
    if (!dirty) {
      action();
      return;
    }
    if (document.activeElement instanceof HTMLElement)
      returnFocus.current = document.activeElement;
    setPendingLeave(() => action);
  };

  const leave = () => {
    setPendingLeave(null);
    setDirty(false);
    setEditing(false);
  };

  if (outlines.isLoading) return <div className="loading">正在加载提纲…</div>;
  if (outlines.isError || !outlines.data) {
    return (
      <Card className="card-pad">
        <ErrorState
          message={outlines.error?.message ?? "提纲加载失败"}
          onRetry={() => void outlines.refetch()}
        />
      </Card>
    );
  }

  const body = outlines.data;
  const facts = digest.data?.facts ?? [];
  const factsById = new Map(facts.map((fact) => [fact.id, fact]));
  const canConfirm =
    Boolean(selected) && selected.is_latest && selected.status !== "confirmed";

  const request = (force: boolean) =>
    generate.mutate(force, {
      onSuccess: () =>
        showToast({
          type: "info",
          content: force ? "已发起重新生成。" : "已创建提纲生成任务。",
        }),
      onError: (error) => showToast({ type: "error", content: error.message }),
    });

  return (
    <>
      <Card className="card-pad">
        <div className="toolbar">
          <h2>提纲</h2>
          <div className="toolbar">
            <Button
              disabled={generate.isPending}
              onClick={() => request(false)}
            >
              生成提纲
            </Button>
            <Button
              className="secondary"
              disabled={generate.isPending}
              onClick={() => request(true)}
            >
              重新生成
            </Button>
          </div>
        </div>
        <TaskStatusBanner
          status={body.task_status}
          queuedText="提纲生成已排队，等待 worker 领取"
          runningText="正在生成提纲…"
          doneText={
            versions.length > 0
              ? `生成完成：共 ${versions.length} 个版本`
              : "生成完成"
          }
          failedText="上次生成失败"
          retryLabel="重新生成"
          busy={generate.isPending}
          onRetry={
            body.task_status === "failed" ? () => request(true) : undefined
          }
        />
        {generate.isError && (
          <div className="field-error" role="alert">
            {generate.error.message}
          </div>
        )}
        {body.has_blocking && (
          <div className="alert" role="alert">
            {`还有 ${body.blocking_count} 条阻塞缺口未处理：可以继续确认提纲，但按现有资料写主线会有风险（是否允许生成全文由下一阶段判断）。`}
          </div>
        )}
        {body.has_unconfirmed_changes && (
          <div className="alert" role="status">
            {
              "已确认的版本不是最新版本：最新版本是草稿，需要重新确认后才会被当作当前提纲。"
            }
          </div>
        )}
        {versions.length === 0 ? (
          <EmptyState
            title="还没有提纲"
            description="点「生成提纲」依据最新的资料包与已核对事实生成分节提纲；生成后可以逐节修改并确认。"
          />
        ) : (
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
                  <Button
                    className="secondary"
                    onClick={() => {
                      setSelectedId(version.id);
                      setEditing(false);
                    }}
                  >
                    查看这一版
                  </Button>
                  <Button
                    className="secondary"
                    onClick={() => {
                      guard(() => {
                        setSelectedId(version.id);
                        setEditing(true);
                      });
                    }}
                  >
                    编辑这一版
                  </Button>
                  {version.is_latest && version.status !== "confirmed" && (
                    <Button
                      disabled={confirm.isPending}
                      onClick={() =>
                        confirm.mutate(version.id, {
                          onSuccess: () =>
                            showToast({
                              type: "success",
                              content: `已确认第 ${version.version_number} 版提纲。`,
                            }),
                          onError: (error) =>
                            showToast({
                              type: "error",
                              content: error.message,
                            }),
                        })
                      }
                    >
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
        )}
      </Card>
      {selected && (
        <Card className="card-pad">
          <div className="toolbar">
            <h2>
              第 {selected.version_number} 版{editing ? "（编辑中）" : ""}
            </h2>
            {editing ? (
              <span className="muted">
                保存会另存为新草稿版本；引文由所选事实自动带出
              </span>
            ) : (
              <div className="toolbar">
                <Button
                  className="secondary"
                  onClick={() => guard(() => setEditing(true))}
                >
                  编辑
                </Button>
                {canConfirm && (
                  <Button
                    disabled={confirm.isPending}
                    onClick={() =>
                      confirm.mutate(selected.id, {
                        onSuccess: () =>
                          showToast({
                            type: "success",
                            content: `已确认第 ${selected.version_number} 版提纲。`,
                          }),
                        onError: (error) =>
                          showToast({ type: "error", content: error.message }),
                      })
                    }
                  >
                    确认这一版
                  </Button>
                )}
              </div>
            )}
          </div>
          {editing ? (
            <OutlineEditor
              version={selected}
              facts={facts}
              saving={save.isPending}
              error={save.isError ? save.error.message : undefined}
              onCancel={() => guard(() => setEditing(false))}
              onSave={(payload) =>
                save.mutate(
                  { outlineId: selected.id, body: payload },
                  {
                    onSuccess: (result) => {
                      setDirty(false);
                      setEditing(false);
                      setSelectedId(result.version.id);
                      showToast({
                        type: "success",
                        content: `已保存为第 ${result.version.version_number} 版草稿。`,
                      });
                    },
                    onError: (error) =>
                      showToast({ type: "error", content: error.message }),
                  },
                )
              }
              onDirtyChange={setDirty}
            />
          ) : (
            <>
              <h3>{selected.title || "（无标题）"}</h3>
              {selected.angle && <p className="muted">{selected.angle}</p>}
              <ol className="outline-read">
                {selected.sections.map((section, index) => (
                  <li key={index}>
                    <strong>{section.heading}</strong>
                    {section.talking_points.length > 0 && (
                      <ul className="reason-list">
                        {section.talking_points.map((point) => (
                          <li key={point}>{point}</li>
                        ))}
                      </ul>
                    )}
                    <div className="outline-facts">
                      {section.fact_ids.length === 0 ? (
                        <span className="field-error">这一节没有事实依据</span>
                      ) : (
                        section.fact_ids.map((factId) => {
                          const fact = factsById.get(factId);
                          return (
                            <div key={factId} className="outline-fact">
                              <Badge
                                tone={
                                  fact?.status === "supported"
                                    ? "success"
                                    : "warning"
                                }
                              >
                                {fact?.status === "missing"
                                  ? "缺料待核对"
                                  : "依据事实"}
                              </Badge>
                              <span>
                                {fact?.statement ??
                                  `事实 ${factId.slice(0, 8)}`}
                              </span>
                            </div>
                          );
                        })
                      )}
                    </div>
                    {section.impact_notes.length > 0 && (
                      <div>
                        <span className="muted">行业影响分析</span>
                        <ul className="reason-list">
                          {section.impact_notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </li>
                ))}
              </ol>
              <p className="muted">
                生成信息：{selected.model ?? "（人工保存，无模型）"}
                {selected.prompt_version &&
                  ` · 提示词 ${selected.prompt_version}`}
                {selected.digest_run_id &&
                  ` · 资料包 ${selected.digest_run_id.slice(0, 8)}`}
              </p>
            </>
          )}
        </Card>
      )}
      <UnsavedChanges dirty={dirty} />
      <UnsavedDialog
        open={pendingLeave !== null}
        onKeep={() => setPendingLeave(null)}
        onDiscard={() => {
          const action = pendingLeave;
          leave();
          action?.();
        }}
        returnFocus={returnFocus}
      />
    </>
  );
}
