import { useId, useState } from "react";
import { providerStatus, type Provider } from "./types";

/**
 * 左栏供应商列表：每行展示状态点、名称和状态文字；
 * 点击切换右栏详情，列表顺序与服务端返回一致（创建时间）。
 */
export function ProviderList({
  providers,
  selectedId,
  onSelect,
}: {
  providers: Provider[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const titleId = useId();
  const [nowSeconds] = useState(() => Math.floor(Date.now() / 1000));
  return (
    <nav className="card provider-list-card" aria-labelledby={titleId}>
      <h2 id={titleId}>供应商</h2>
      <ul className="provider-list">
        {providers.map((provider) => {
          const status = providerStatus(provider, nowSeconds);
          return (
            <li key={provider.id}>
              <button
                type="button"
                className={
                  provider.id === selectedId
                    ? "provider-list-item selected"
                    : "provider-list-item"
                }
                aria-current={provider.id === selectedId || undefined}
                onClick={() => onSelect(provider.id)}
              >
                <span className="provider-list-name">
                  <span
                    className={
                      status.tone === "ok"
                        ? "status-dot ok"
                        : status.tone === "warn"
                          ? "status-dot warn"
                          : "status-dot off"
                    }
                    aria-hidden="true"
                  />
                  {provider.name}
                </span>
                <span className="muted">{status.label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
