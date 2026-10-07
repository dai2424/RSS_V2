import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button, EmptyState } from "./ui";

describe("基础界面组件", () => {
  it("显示空态和可访问按钮", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <>
        <EmptyState title="暂无来源" description="添加一个 RSS 来源" />
        <Button onClick={onClick}>新增来源</Button>
      </>,
    );
    expect(screen.getByText("暂无来源")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "新增来源" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
