import { useCallback, useEffect, useRef } from "react";

/**
 * 把高频调用压成一次：停止触发 delay 毫秒后才真正执行。
 *
 * 用在输入框驱动请求的场景：消息列表的全文检索是全表扫文本，逐字发请求既浪费，
 * 也会让列表在输入过程中反复闪烁。回调始终使用最新一次渲染的闭包，
 * 所以里面读到的筛选参数不会过期。
 */
export function useDebouncedCallback<Args extends unknown[]>(
  callback: (...args: Args) => void,
  delay = 300,
): (...args: Args) => void {
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef(callback);
  useEffect(() => {
    latest.current = callback;
  }, [callback]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return useCallback(
    (...args: Args) => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => latest.current(...args), delay);
    },
    [delay],
  );
}
