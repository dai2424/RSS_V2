import { percent } from "./KeywordCharts";

describe("关键词占比展示", () => {
  it("给出保留一位小数的占比", () => {
    expect(percent(1, 4)).toBe("25.0%");
    expect(percent(84, 93)).toBe("90.3%");
  });
  it("分母为零时不显示百分比，避免出现 NaN", () => {
    expect(percent(0, 0)).toBe("—");
  });
});
