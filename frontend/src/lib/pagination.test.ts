import {
  ELLIPSIS,
  normalizeSize,
  offsetFor,
  pageCountOf,
  pageItems,
  pageOfOffset,
  sizeChangeOffset,
} from "./pagination";

describe("每页条数与页码换算", () => {
  it("只接受下拉里给出的每页条数", () => {
    expect(normalizeSize(50)).toBe(50);
    expect(normalizeSize(100)).toBe(100);
    // 手写或过期的 URL 参数回落到默认值，而不是把非法的 limit 发给接口。
    expect(normalizeSize(null)).toBe(25);
    expect(normalizeSize(7)).toBe(25);
    expect(normalizeSize(Number.NaN)).toBe(25);
  });

  it("总页数按总数向上取整，空列表也算一页", () => {
    expect(pageCountOf(0, 25)).toBe(1);
    expect(pageCountOf(25, 25)).toBe(1);
    expect(pageCountOf(26, 25)).toBe(2);
    expect(pageCountOf(1400, 100)).toBe(14);
  });

  it("页码与偏移可以互相换算", () => {
    expect(pageOfOffset(0, 25)).toBe(1);
    expect(pageOfOffset(25, 25)).toBe(2);
    expect(pageOfOffset(30, 25)).toBe(2);
    expect(offsetFor(1, 25)).toBe(0);
    expect(offsetFor(3, 50)).toBe(100);
    expect(offsetFor(0, 25)).toBe(0);
  });
});

describe("切换每页条数", () => {
  it("保留当前页码，按新的每页条数重算偏移", () => {
    // 第 3 页（25 条一页）换成 50 条一页后仍是第 3 页。
    expect(sizeChangeOffset(50, 25, 50, 1400)).toBe(100);
    expect(sizeChangeOffset(100, 50, 25, 1400)).toBe(50);
  });

  it("页码超出新的末页时夹紧到最后一页", () => {
    // 共 60 条时第 3 页（每页 25）换成每页 100 只剩 1 页。
    expect(sizeChangeOffset(50, 25, 100, 60)).toBe(0);
    // 共 260 条时第 6 页（每页 50）换成每页 100 只有 3 页。
    expect(sizeChangeOffset(250, 50, 100, 260)).toBe(200);
  });
});

describe("页码列表", () => {
  it("页数不多时全部列出", () => {
    expect(pageItems(1, 1)).toEqual([1]);
    expect(pageItems(3, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(pageItems(7, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("当前页在中间时折叠两侧", () => {
    expect(pageItems(10, 20)).toEqual([1, ELLIPSIS, 9, 10, 11, ELLIPSIS, 20]);
  });

  it("靠近首页时补足前五页并折叠末尾", () => {
    expect(pageItems(1, 20)).toEqual([1, 2, 3, 4, 5, ELLIPSIS, 20]);
    expect(pageItems(4, 8)).toEqual([1, 2, 3, 4, 5, ELLIPSIS, 8]);
  });

  it("靠近末页时补足最后五页并折叠开头", () => {
    expect(pageItems(20, 20)).toEqual([1, ELLIPSIS, 16, 17, 18, 19, 20]);
    expect(pageItems(17, 20)).toEqual([1, ELLIPSIS, 16, 17, 18, 19, 20]);
  });

  it("始终是 7 个格子，翻页时不抖动", () => {
    for (const page of [1, 2, 5, 10, 19, 20]) {
      expect(pageItems(page, 20)).toHaveLength(7);
    }
  });
});
