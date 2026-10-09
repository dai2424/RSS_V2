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
/** 关键词类型：与后端 entity/topic/event 对应，用于徽标提示。 */
export const keywordKindLabels: Record<string, string> = {
  entity: "实体：公司、产品、人物、机构、地点等具名对象",
  topic: "主题：能概括话题的领域词",
  event: "事件：本条消息里发生的事",
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
/** 任务耗时：按量级给到秒 / 分 / 时，负数与非法输入算未知。 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "未知";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 时 ${Math.floor((seconds % 3600) / 60)} 分`;
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
type GeneratedText = {
  status: string;
  title: string | null;
  summary: string | null;
};
/**
 * 列表展示文本：加工结果优先于译文，都不可用时回退原文。
 * machine 用于标记内容由模型生成，避免与原文混淆。
 */
export function preferredText(
  field: "title" | "summary",
  enrichment: GeneratedText | undefined,
  translation: GeneratedText | undefined,
  fallback: string | null | undefined,
): { text: string; machine: boolean } {
  for (const source of [enrichment, translation]) {
    const value = source?.status === "succeeded" ? source[field]?.trim() : "";
    if (value) return { text: value, machine: true };
  }
  return { text: (fallback ?? "").trim(), machine: false };
}
