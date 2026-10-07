import { useEffect, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info } from "lucide-react";

export type ToastType = "success" | "error" | "info";

type ToastItem = {
  id: number;
  type: ToastType;
  content: ReactNode;
  text: string | null;
  leaving: boolean;
};

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const VISIBLE_MS = 3200;
const EXIT_MS = 200;

function notify() {
  for (const listener of listeners) listener();
}

/** 标记离开并播放退出动画，动画结束后从列表移除。 */
function dismiss(id: number) {
  const item = items.find((t) => t.id === id);
  if (!item || item.leaving) return;
  item.leaving = true;
  notify();
  window.setTimeout(() => {
    items = items.filter((t) => t.id !== id);
    notify();
  }, EXIT_MS);
}

/**
 * 顶部居中弹出提示（类似 ElementUI Message），不打断文档流，自动消失。
 * 成功/信息用 role=status，错误用 role=alert，保证辅助技术可感知；
 * 相同文本未消失前不重复堆叠。
 */
export function showToast(input: { type: ToastType; content: ReactNode }) {
  const text = typeof input.content === "string" ? input.content : null;
  if (
    text &&
    items.some((t) => !t.leaving && t.type === input.type && t.text === text)
  ) {
    return;
  }
  const item: ToastItem = {
    id: nextId++,
    type: input.type,
    content: input.content,
    text,
    leaving: false,
  };
  items = [...items, item];
  notify();
  window.setTimeout(() => dismiss(item.id), VISIBLE_MS);
}

const icons: Record<ToastType, typeof Info> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};

/** 全局提示容器，挂在应用外壳顶层；浮层本身不拦截点击，链接除外。 */
export function ToastHost() {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((v) => v + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return (
    <div className="toast-host">
      {items.map((item) => {
        const Icon = icons[item.type];
        return (
          <div
            key={item.id}
            role={item.type === "error" ? "alert" : "status"}
            className={`toast toast-${item.type}${item.leaving ? " toast-leave" : ""}`}
          >
            <Icon size={16} aria-hidden />
            <div className="toast-content">{item.content}</div>
          </div>
        );
      })}
    </div>
  );
}
