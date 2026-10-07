export const statusLabels: Record<string, string> = {
  queued: "已排队",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  pending: "待翻译",
  en: "英文",
  zh: "中文",
  auto: "自动识别",
  mixed: "混合",
};
export function formatTime(value: number | string | null | undefined): string {
  if (!value) return "未知";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return String(value);
  return (
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      dateStyle: "short",
      timeStyle: "short",
    }).format(date) + " 上海"
  );
}
export function safeLink(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return ["http:", "https:"].includes(parsed.protocol) ? url : undefined;
  } catch {
    return undefined;
  }
}
/** 列表展示用域名；解析失败时回退为原文，完整地址通过 title 提示。 */
export function domainOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
