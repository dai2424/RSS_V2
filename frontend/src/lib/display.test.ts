import { formatTime, safeLink } from "./display";

describe("原文链接与时间展示", () => {
  it.each(["javascript:alert(1)", "data:text/html,bad", "file:///tmp/feed"])(
    "不将 %s 转为可执行外链",
    (url) => {
      expect(safeLink(url)).toBeUndefined();
    },
  );
  it("允许 HTTP(S) 原文链接", () =>
    expect(safeLink("https://example.test/article")).toBe(
      "https://example.test/article",
    ));
  it("缺失发布时间保持未知，UTC 时间显示上海时区", () => {
    expect(formatTime(null)).toBe("未知");
    expect(formatTime(1791331200)).toContain("8:00");
    expect(formatTime(1791331200)).toContain("上海");
  });
});
