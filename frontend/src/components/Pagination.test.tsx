import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Pagination } from "./Pagination";

/** 分页条的交互：总数、页码直选、每页条数和跳页都要能用键盘完成。 */
describe("分页条", () => {
  const props = () => ({
    total: 1400,
    offset: 0,
    size: 25,
    onPage: vi.fn(),
    onSize: vi.fn(),
  });

  it("显示总数，并把当前页标成 aria-current", () => {
    render(<Pagination {...props()} offset={100} />);
    expect(screen.getByText(/共 1400 条/)).toBeVisible();
    expect(screen.getByText("5")).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: "第 5 页" })).toBeNull();
  });

  it("第一页禁用上一页，最后一页禁用下一页", () => {
    const { unmount } = render(<Pagination {...props()} />);
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
    unmount();
    render(<Pagination {...props()} offset={1375} />);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();
  });

  it("点页码和下一页都回调新的偏移", async () => {
    const user = userEvent.setup();
    const onPage = vi.fn();
    render(<Pagination {...props()} offset={100} onPage={onPage} />);
    await user.click(screen.getByRole("button", { name: "第 4 页" }));
    expect(onPage).toHaveBeenLastCalledWith(75);
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(onPage).toHaveBeenLastCalledWith(125);
  });

  it("改每页条数回传新的条数", async () => {
    const user = userEvent.setup();
    const onSize = vi.fn();
    render(<Pagination {...props()} onSize={onSize} />);
    await user.selectOptions(screen.getByLabelText("每页条数"), "100");
    expect(onSize).toHaveBeenCalledWith(100);
  });

  it("跳页输入回车才生效，非法输入不动", async () => {
    const user = userEvent.setup();
    const onPage = vi.fn();
    render(<Pagination {...props()} onPage={onPage} />);
    const jump = screen.getByLabelText("跳至页码");
    await user.type(jump, "7");
    expect(onPage).not.toHaveBeenCalled();
    await user.type(jump, "{Enter}");
    expect(onPage).toHaveBeenLastCalledWith(150);
    await user.clear(jump);
    await user.type(jump, "0{Enter}");
    expect(onPage).toHaveBeenLastCalledWith(150);
    await user.clear(jump);
    await user.type(jump, "999{Enter}");
    expect(onPage).toHaveBeenLastCalledWith(1375);
  });

  it("请求在途时禁用全部控件", () => {
    render(<Pagination {...props()} disabled />);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    expect(screen.getByLabelText("每页条数")).toBeDisabled();
    expect(screen.getByLabelText("跳至页码")).toBeDisabled();
  });

  it("词表用「个词」作量词", () => {
    render(<Pagination {...props()} total={3} unit="个词" />);
    expect(screen.getByText(/共 3 个词/)).toBeVisible();
  });
});
