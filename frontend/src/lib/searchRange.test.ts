import {
  boundsOf,
  customRangeOf,
  defaultCustomRange,
  rangeLabel,
  rangeOf,
  resolveRange,
  shanghaiToday,
  sinceOf,
  withDate,
} from "./searchRange";

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

describe("自定义日期范围", () => {
  it("按上海时区取今天，默认范围是最近 7 天", () => {
    // 2026-10-08T17:00Z 在上海是 2026-10-09 01:00。
    expect(shanghaiToday(new Date("2026-10-08T17:00:00Z"))).toBe("2026-10-09");
    expect(defaultCustomRange("2026-10-09")).toEqual({
      from: "2026-10-03",
      to: "2026-10-09",
    });
  });
  it("缺一个、格式不对都按没有处理；顺序颠倒时交换", () => {
    expect(customRangeOf("2026-10-01", null)).toBeNull();
    expect(customRangeOf("2026-10-01", "10-07")).toBeNull();
    expect(customRangeOf("2026-10-01", "2026-10-07")).toEqual({
      from: "2026-10-01",
      to: "2026-10-07",
    });
    expect(customRangeOf("2026-10-07", "2026-10-01")).toEqual({
      from: "2026-10-01",
      to: "2026-10-07",
    });
  });
  it("边界按上海时区的自然日换算成 UTC 秒", () => {
    const bounds = boundsOf({ from: "2026-10-01", to: "2026-10-07" });
    expect(new Date(bounds.since * 1000).toISOString()).toBe(
      "2026-09-30T16:00:00.000Z",
    );
    expect(new Date(bounds.until * 1000).toISOString()).toBe(
      "2026-10-07T15:59:59.000Z",
    );
  });
});

describe("范围解析与日期端点", () => {
  const now = 1_800_000_000;
  it("自定义日期优先于预设范围，并给出 UTC 秒边界", () => {
    const resolved = resolveRange(
      { range: "7d", from: "2026-10-01", to: "2026-10-07" },
      true,
      now,
    );
    expect(resolved.value).toBe("custom");
    expect(resolved.since).toBe(1_790_784_000);
    expect(resolved.until).toBe(1_791_388_799);
    expect(resolved.custom).toEqual({ from: "2026-10-01", to: "2026-10-07" });
  });
  it("没有自定义日期时沿用预设规则", () => {
    const resolved = resolveRange(
      { range: null, from: null, to: null },
      true,
      now,
    );
    expect(resolved.value).toBe("7d");
    expect(resolved.since).toBe(now - 7 * 86400);
    expect(resolved.custom).toBeNull();
  });
  it("把日期点反时拉齐另一端，保持有效区间", () => {
    const range = { from: "2026-10-01", to: "2026-10-07" };
    expect(withDate(range, "to", "2026-09-20")).toEqual({
      from: "2026-09-20",
      to: "2026-09-20",
    });
    expect(withDate(range, "from", "2026-10-20")).toEqual({
      from: "2026-10-20",
      to: "2026-10-20",
    });
    expect(withDate(range, "to", "2026-10-09")).toEqual({
      from: "2026-10-01",
      to: "2026-10-09",
    });
  });
});
