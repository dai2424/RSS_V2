import { describe, expect, it } from "vitest";
import {
  DEFAULT_DISCOVERY_RANGE,
  discoveryRangeLabel,
  discoveryWindow,
} from "./discoveryWindow";
import { CUSTOM_VALUE } from "./searchRange";

const NOW = 1791942293; // 2026-10-14 09:44:53 +08

describe("discoveryWindow", () => {
  it("默认窗口是最近 7 天，终点取请求时刻", () => {
    const window = discoveryWindow(DEFAULT_DISCOVERY_RANGE, { from: "", to: "" }, NOW);
    expect(window.window_end).toBe(NOW);
    expect(window.window_start).toBe(NOW - 7 * 86400);
  });

  it("30/90 天按同样口径换算", () => {
    expect(discoveryWindow("30d", { from: "", to: "" }, NOW).window_start).toBe(NOW - 30 * 86400);
    expect(discoveryWindow("90d", { from: "", to: "" }, NOW).window_start).toBe(NOW - 90 * 86400);
  });

  it("指定日期按上海时区自然日取边界", () => {
    const window = discoveryWindow(
      CUSTOM_VALUE,
      { from: "2026-10-08", to: "2026-10-09" },
      NOW,
    );
    // 2026-10-08 00:00 +08 = 1791388800；10-09 23:59:59 +08 = 1791561599
    expect(window.window_start).toBe(1791388800);
    expect(window.window_end).toBe(1791561599);
  });

  it("范围文案与后端默认口径一致", () => {
    expect(discoveryRangeLabel("7d")).toBe("最近 7 天");
    expect(discoveryRangeLabel("30d")).toBe("最近 30 天");
    expect(discoveryRangeLabel(CUSTOM_VALUE)).toBe("指定日期");
  });
});
