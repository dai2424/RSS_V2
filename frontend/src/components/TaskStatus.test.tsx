import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TaskStatusBanner } from "./TaskStatus";

describe("TaskStatusBanner", () => {
  it("排队与运行中给转圈提示与说明", () => {
    render(<TaskStatusBanner status="queued" />);
    expect(screen.getByRole("status")).toHaveTextContent("等待 worker 领取");

    render(<TaskStatusBanner status="running" runningText="正在整理资料…" />);
    expect(screen.getByText("正在整理资料…")).toBeInTheDocument();
  });

  it("失败时给出原因入口与重试按钮", () => {
    const onRetry = vi.fn();
    render(
      <TaskStatusBanner
        status="failed"
        onRetry={onRetry}
        retryLabel="重新整理"
      />,
    );
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("上次执行失败");
    expect(banner).toHaveTextContent("任务页");
    screen.getByRole("button", { name: "重新整理" }).click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("完成时给明确确认，未运行时整条不渲染", () => {
    render(
      <TaskStatusBanner status="succeeded" doneText="整理完成：事实 4 条" />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("整理完成：事实 4 条");

    const { container } = render(<TaskStatusBanner status={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
