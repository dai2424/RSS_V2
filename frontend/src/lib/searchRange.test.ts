import { rangeLabel, rangeOf, sinceOf } from "./searchRange";

describe("消息检索的时间范围", () => {
  it("没显式选过时：搜索默认近 7 天，浏览默认全部", () => {
    expect(rangeOf(null, true)).toBe("7d");
    expect(rangeOf(null, false)).toBe("all");
  });
  it("显式选过的范围优先，非法值回退到默认规则", () => {
    expect(rangeOf("30d", true)).toBe("30d");
    expect(rangeOf("all", true)).toBe("all");
    expect(rangeOf("1y", true)).toBe("7d");
    expect(rangeOf("1y", false)).toBe("all");
  });
  it("按范围换算出 since，全部时间不传下界", () => {
    const now = 1_800_000_000;
    expect(sinceOf("7d", now)).toBe(now - 7 * 86400);
    expect(sinceOf("90d", now)).toBe(now - 90 * 86400);
    expect(sinceOf("all", now)).toBeUndefined();
  });
  it("标签用于界面提示", () => {
    expect(rangeLabel("7d")).toBe("近 7 天");
    expect(rangeLabel("all")).toBe("全部时间");
  });
});
