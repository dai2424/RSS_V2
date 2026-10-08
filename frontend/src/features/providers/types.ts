import { useEffect } from "react";
import type { components } from "../../api/generated";

export type Provider = components["schemas"]["ProviderResponse"];
export type ProviderModel = components["schemas"]["ProviderModelResponse"];
export type Key = components["schemas"]["ProviderKeyResponse"];
export type ConnectionTest = components["schemas"]["ConnectionTestResponse"];
export type DirtyChange = (id: string, dirty: boolean) => void;

/** 聚合页面内多个表单的未提交状态，卸载时清除对应状态。 */
export function useFormDirty(
  id: string,
  dirty: boolean,
  onDirtyChange: DirtyChange,
) {
  useEffect(() => {
    onDirtyChange(id, dirty);
    return () => onDirtyChange(id, false);
  }, [id, dirty, onDirtyChange]);
}

/** 供应商状态汇总；颜色只辅助表达，文案始终可见。 */
export type ProviderStatus = { tone: "ok" | "warn" | "off"; label: string };

/**
 * 从真实调用记录推导供应商状态：未启用或未配置 Key 为灰色，
 * 存在成功且未冷却的 Key 为绿色，否则为橙色（上次失败或冷却中）。
 */
export function providerStatus(
  provider: Provider,
  currentSeconds: number,
): ProviderStatus {
  if (!provider.enabled) return { tone: "off", label: "已停用" };
  const usable = provider.keys.filter(
    (key) =>
      key.enabled &&
      (key.cooldown_until === null || key.cooldown_until <= currentSeconds),
  );
  if (usable.length === 0) {
    return {
      tone: "off",
      label: provider.keys.length === 0 ? "未配置 Key" : "Key 冷却中",
    };
  }
  if (usable.some((key) => key.last_status === "succeeded")) {
    return { tone: "ok", label: "上次调用成功" };
  }
  return {
    tone: "warn",
    label: usable.some((key) => key.last_status) ? "上次调用失败" : "尚未调用",
  };
}

/** 密钥行状态文字：最近一次调用结果与冷却，颜色只辅助表达。 */
export function keyStatusLabel(key: Key, currentSeconds: number): string {
  const cooling =
    key.cooldown_until !== null && key.cooldown_until > currentSeconds;
  if (!key.last_status) return key.enabled ? "未使用" : "未使用（已停用）";
  if (key.last_status === "succeeded") return "上次调用成功";
  return cooling ? "上次调用失败，冷却中" : "上次调用失败";
}
