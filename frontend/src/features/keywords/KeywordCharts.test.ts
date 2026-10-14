import { composition, percent, singletonSummary } from "./KeywordCharts";

describe("关键词占比展示", () => {
  it("给出保留一位小数的占比", () => {
    expect(percent(1, 4)).toBe("25.0%");
    expect(percent(84, 93)).toBe("90.3%");
  });
  it("分母为零时不显示百分比，避免出现 NaN", () => {
    expect(percent(0, 0)).toBe("—");
  });
});

describe("图表文案的数字来源", () => {
  it("类型构成按标签取值，接口换顺序也不串位", () => {
    const data = [
      { label: "实体", value: 284 },
      { label: "事件", value: 63 },
      { label: "主题", value: 405 },
    ];
    expect(composition(data)).toBe("实体 284 个、事件 63 个、主题 405 个。");
  });
  it("长尾占比用全量词表做分母，并写明分母是多少", () => {
    const data = [
      { label: "只出现一次", value: 672 },
      { label: "出现 2 次", value: 53 },
    ];
    expect(singletonSummary(data, 752)).toBe(
      "只出现一次的写法有 672 个，占 89.4%（全量词表 752 个）。",
    );
  });
  it("长尾分档为空时给出明确文案", () => {
    expect(singletonSummary([], 0)).toBe("还没有长尾数据。");
  });
});
