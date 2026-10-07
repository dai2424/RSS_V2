import { useEffect } from "react";
import type { components } from "../../api/generated";

export type Provider = components["schemas"]["ProviderResponse"];
export type Key = components["schemas"]["ProviderKeyResponse"];
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
