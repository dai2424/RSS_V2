import { domainOf, formatDuration, formatTime, safeLink } from "./display";

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
  it("来源列表只显示域名，非法地址回退原文", () => {
    expect(domainOf("https://www.v2ex.com/index.xml")).toBe("www.v2ex.com");
    expect(domainOf("not a url")).toBe("not a url");
  });
  it("缺失发布时间保持未知，UTC 时间显示上海时区", () => {
    expect(formatTime(null)).toBe("未知");
    expect(formatTime(1791331200)).toContain("8:00");
    expect(formatTime(1791331200)).toContain("上海");
  });
});

describe("任务耗时展示", () => {
  it.each([
    [0, "0 秒"],
    [42, "42 秒"],
    [59, "59 秒"],
    [60, "1 分 0 秒"],
    [125, "2 分 5 秒"],
    [3600, "1 时 0 分"],
    [7325, "2 时 2 分"],
  ])("把 %s 秒写成 %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });
  it("负数与非法值不伪装成正常耗时", () => {
    expect(formatDuration(-1)).toBe("未知");
    expect(formatDuration(Number.NaN)).toBe("未知");
  });
});
