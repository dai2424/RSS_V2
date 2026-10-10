import { act, renderHook } from "@testing-library/react";
import { useDebouncedCallback } from "./useDebouncedCallback";

describe("防抖回调", () => {
  it("连续触发只执行最后一次，并且用的是最新的闭包", () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const { result, rerender } = renderHook(
      ({ suffix }: { suffix: string }) =>
        useDebouncedCallback(
          (value: string) => calls.push(value + suffix),
          300,
        ),
      { initialProps: { suffix: "-旧" } },
    );
    act(() => {
      result.current("华");
      result.current("华为");
      result.current("华为乾");
    });
    rerender({ suffix: "-新" });
    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(calls).toEqual([]);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(calls).toEqual(["华为乾-新"]);
    vi.useRealTimers();
  });

  it("卸载后待执行的调用不再触发", () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const { result, unmount } = renderHook(() =>
      useDebouncedCallback((value: string) => calls.push(value), 300),
    );
    act(() => {
      result.current("华为");
    });
    unmount();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(calls).toEqual([]);
    vi.useRealTimers();
  });
});
