import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, requireResponse } from "../../api/client";
import { Button, Card, ErrorState } from "../../components/ui";
import { usePromptActions, type Prompt } from "./usePrompts";

/** 试跑：选一条已有消息或手填样例，真实调用一次模型并展示结果。 */
export function PromptTestPanel({ prompt }: { prompt: Prompt }) {
  const [mode, setMode] = useState<"message" | "manual">("message");
  const [versionId, setVersionId] = useState("");
  const [sample, setSample] = useState({ title: "", summary: "", content: "" });
  const { test } = usePromptActions(prompt.task_kind);
  const messages = useQuery({
    queryKey: ["messages", "prompt-sample"],
    queryFn: async () => {
      const r = await api.GET("/api/messages", {
        params: { query: { limit: 50 } },
      });
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const options = (messages.data ?? []).flatMap((message) =>
    message.latest_version
      ? [{ id: message.latest_version.id, label: message.latest_version.title }]
      : [],
  );
  const run = () =>
    test.mutate({
      promptId: prompt.id,
      body:
        mode === "message"
          ? { message_version_id: versionId || options[0]?.id }
          : { sample },
    });
  const result = test.data;
  return (
    <Card className="card-pad">
      <h2>试跑</h2>
      <p className="muted">
        用真实模型跑一次，结果只用于预览：会写入调用审计，但不会保存成译文或摘要。
      </p>
      <p className="muted">
        试跑算一次使用：跑过的版本不能再就地编辑或删除，只能另存为新版本或归档。
      </p>
      <div className="form-field">
        <label htmlFor="prompt-sample-mode">样例来源</label>
        <select
          id="prompt-sample-mode"
          value={mode}
          onChange={(e) =>
            setMode(e.target.value === "manual" ? "manual" : "message")
          }
        >
          <option value="message">从已有消息选择</option>
          <option value="manual">手动填写样例</option>
        </select>
      </div>
      {mode === "message" ? (
        <div className="form-field">
          <label htmlFor="prompt-sample-message">样例消息</label>
          <select
            id="prompt-sample-message"
            value={versionId || options[0]?.id || ""}
            onChange={(e) => setVersionId(e.target.value)}
          >
            {options.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
          {options.length === 0 && (
            <p className="muted">还没有消息，先采集或改用手填样例。</p>
          )}
        </div>
      ) : (
        <>
          {(["title", "summary", "content"] as const).map((field) => (
            <div className="form-field" key={field}>
              <label htmlFor={`prompt-sample-${field}`}>
                {field === "title"
                  ? "样例标题"
                  : field === "summary"
                    ? "样例摘要"
                    : "样例正文"}
              </label>
              <textarea
                id={`prompt-sample-${field}`}
                rows={field === "content" ? 6 : 2}
                value={sample[field]}
                onChange={(e) =>
                  setSample({ ...sample, [field]: e.target.value })
                }
              />
            </div>
          ))}
        </>
      )}
      <div className="form-actions">
        <Button
          disabled={
            test.isPending || (mode === "message" && options.length === 0)
          }
          onClick={run}
        >
          {test.isPending ? "试跑中…" : "运行试跑"}
        </Button>
      </div>
      {test.isError && <ErrorState message={test.error.message} />}
      {result && (
        <div
          className={result.ok ? "alert" : "alert alert-error"}
          role="status"
        >
          {result.ok
            ? `试跑成功：${result.model} · ${result.latency_ms} ms · ${result.total_tokens} tokens`
            : `试跑失败（${result.error_code ?? "未知"}）：${result.error_message ?? ""}`}
        </div>
      )}
      {result && (
        <div className="prompt-preview">
          <h3>实际发送的用户提示</h3>
          <pre className="prompt-text">{result.user}</pre>
          <h3>结构化输出</h3>
          <pre className="prompt-text">
            {JSON.stringify(result.output, null, 2)}
          </pre>
        </div>
      )}
    </Card>
  );
}
