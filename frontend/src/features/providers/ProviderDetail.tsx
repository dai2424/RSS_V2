import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, requireResponse } from "../../api/client";
import { showToast } from "../../components/Toast";
import { Button, Card, Switch } from "../../components/ui";
import { KeyAddDialog, KeyRow } from "./KeySettings";
import { ProviderEditDialog } from "./ProviderEditDialog";
import { ProviderModels } from "./ProviderModels";
import {
  protocolLabel,
  providerStatus,
  type ConnectionTest,
  type DirtyChange,
  type Provider,
} from "./types";

/**
 * 右栏供应商详情：连接信息、模型列表与 API Key 三个区块。
 * 区块标题右侧提供“添加”入口（弹层表单）；启用开关即时提交。
 */
export function ProviderDetail({
  provider,
  onSaved,
  onDirtyChange,
}: {
  provider: Provider;
  onSaved: () => void;
  onDirtyChange: DirtyChange;
}) {
  const [editing, setEditing] = useState(false);
  const [nowSeconds] = useState(() => Math.floor(Date.now() / 1000));
  const [tested, setTested] = useState<ConnectionTest | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const status = providerStatus(provider, nowSeconds);
  const toggle = useMutation({
    mutationFn: async () => {
      const r = await api.PATCH("/api/llm/providers/{provider_id}", {
        params: { path: { provider_id: provider.id } },
        body: { enabled: !provider.enabled },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: onSaved,
    onError: (error) => showToast({ type: "error", content: error.message }),
  });
  const test = useMutation({
    mutationFn: async (model?: string) => {
      const r = await api.POST("/api/llm/providers/{provider_id}/test", {
        params: { path: { provider_id: provider.id } },
        body: { model: model ?? null },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: (result) => {
      setTested(result);
      setTestError(null);
      onSaved();
    },
    onError: (error) => {
      setTested(null);
      setTestError(error.message);
    },
  });
  return (
    <Card
      className="card-pad provider-detail"
      role="region"
      aria-label={provider.name}
    >
      <div className="split">
        <div>
          <h2>{provider.name}</h2>
          <span className="muted">{status.label}</span>
        </div>
        <div className="toolbar provider-toolbar">
          <Switch
            checked={provider.enabled}
            label={`启用供应商 ${provider.name}`}
            disabled={toggle.isPending}
            onToggle={() => toggle.mutate()}
          />
          <Button
            className="secondary"
            disabled={test.isPending || provider.models.length === 0}
            onClick={() => test.mutate(undefined)}
          >
            {test.isPending ? "测试中…" : "测试连接"}
          </Button>
          <Button className="secondary" onClick={() => setEditing(true)}>
            编辑连接
          </Button>
        </div>
      </div>
      {toggle.isError && (
        <p className="field-error" role="alert">
          {toggle.error.message}
        </p>
      )}
      {testError && (
        <p className="alert alert-error" role="alert">
          {testError}
        </p>
      )}
      {tested && (
        <p
          className={tested.ok ? "alert alert-success" : "alert alert-error"}
          role="status"
        >
          {tested.ok
            ? `连接成功：${tested.model} · ${tested.latency_ms} ms · ${tested.total_tokens} tokens`
            : `连接失败（${tested.model}）：${tested.error_message}`}
        </p>
      )}
      <section className="provider-section">
        <h3>连接信息</h3>
        <dl className="metadata-list">
          <dt>Base URL</dt>
          <dd>
            <code>{provider.base_url}</code>
          </dd>
          <dt>API 格式</dt>
          <dd>{protocolLabel(provider.protocol)}</dd>
          <dt>超时</dt>
          <dd>{provider.timeout_seconds} 秒</dd>
          <dt>会话头</dt>
          <dd>{provider.session_header_name || "未设置"}</dd>
          <dt>自定义请求头</dt>
          <dd>
            {Object.keys(provider.extra_headers).length === 0
              ? "未设置"
              : JSON.stringify(provider.extra_headers)}
          </dd>
        </dl>
      </section>
      <ProviderModels
        providerId={provider.id}
        models={provider.models}
        testingModel={test.isPending ? (test.variables ?? null) : null}
        onTest={(model) => test.mutate(model)}
        onSaved={onSaved}
        onDirtyChange={onDirtyChange}
      />
      <section className="provider-section">
        <div className="provider-section-head">
          <h3>API Key</h3>
          <KeyAddDialog
            providerId={provider.id}
            onSaved={onSaved}
            onDirtyChange={onDirtyChange}
          />
        </div>
        {provider.keys.length === 0 ? (
          <p className="muted">
            暂无 Key；添加后即可用“测试连接”验证服务是否可用。
          </p>
        ) : (
          <div className="key-list">
            {provider.keys.map((key) => (
              <KeyRow key={key.id} item={key} onSaved={onSaved} />
            ))}
          </div>
        )}
      </section>
      {/* key 按供应商隔离：弹层表单在挂载时读一次初值，换供应商必须重挂，
          否则保存会把上一个供应商的名称与地址写到当前这条记录上。 */}
      <ProviderEditDialog
        key={provider.id}
        provider={provider}
        open={editing}
        onOpenChange={setEditing}
        onSaved={onSaved}
        onDirtyChange={onDirtyChange}
      />
    </Card>
  );
}
