import { act, render, screen } from "@testing-library/react";
import { showToast, ToastHost } from "./Toast";

describe("顶部浮层提示", () => {
  it("弹出后自动消失；错误用 alert 语义，相同文本不重复堆叠", () => {
    vi.useFakeTimers();
    render(<ToastHost />);
    act(() => {
      showToast({ type: "success", content: "已保存。" });
      showToast({ type: "error", content: "保存失败。" });
      showToast({ type: "success", content: "已保存。" });
    });
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("保存失败。");
    act(() => {
      vi.advanceTimersByTime(3600);
    });
    expect(screen.queryByText("已保存。")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
