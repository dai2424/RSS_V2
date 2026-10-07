import { test, expect } from "@playwright/test";

/** 桌面列表状态使用可控响应，验证加载、空态、筛选无结果和读取失败重试。 */
test("桌面来源列表状态与重试保留筛选", async ({ page }, testInfo) => {
  let release = () => {};
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = false;
  await page.route("**/api/sources?**", async (route) => {
    await ready;
    await route.fulfill({
      status: fail ? 503 : 200,
      contentType: "application/json",
      body: JSON.stringify(
        fail
          ? { code: "unavailable", message: "读取来源暂不可用" }
          : { items: [], total: 0, limit: 25, offset: 0 },
      ),
    });
  });
  try {
    await page.goto("/sources");
    await expect(
      page.getByText("正在加载来源…", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("complementary", { name: "主导航" }),
    ).toBeVisible();
    release();
    await expect(
      page.getByText("还没有 RSS 来源", { exact: true }),
    ).toBeVisible();
    const search = page.getByRole("textbox", { name: "搜索来源", exact: true });
    await search.fill("桌面验收");
    await expect(page.getByText("没有匹配结果", { exact: true })).toBeVisible();
    fail = true;
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("读取来源暂不可用");
    await expect(search).toHaveValue("桌面验收");
    await page.screenshot({
      path: testInfo.outputPath("sources-error-1440.png"),
      fullPage: true,
    });
    fail = false;
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await expect(page.getByRole("alert")).toBeHidden();
    await expect(search).toHaveValue("桌面验收");
    await expect(page.getByText("没有匹配结果", { exact: true })).toBeVisible();
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 1440);
  } finally {
    release();
  }
});
