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

/** 任务列表：目标、执行参数与失败原因都要能看清（响应打桩，不写库）。 */
test("任务列表展示目标、执行参数与失败原因", async ({ page }, testInfo) => {
  const failed = {
    id: "610b540e-a23a-44b1-bd43-95068f795950",
    task_type: "enrich_message",
    idempotency_key: "enrich:example",
    status: "failed",
    attempts: 2,
    lease_until: null,
    input_version_id: "f5a7c65d-c6b5-4373-a5c5-dd5b930c9bb9",
    output_version_id: null,
    error_code: "prompt_missing",
    error_message: "任务快照的提示词已不存在，请重新创建任务",
    created_at: 1759999000,
    updated_at: 1759999125,
    model: "deepseek-v4.1-flash",
    prompt_version: "enrich-v2",
    target_kind: "message",
    target_id: "f5a7c65d-c6b5-4373-a5c5-dd5b930c9bb9",
    target_label: "示例消息标题",
  };
  await page.route("**/api/tasks?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([failed]),
    }),
  );
  await page.goto("/tasks");
  const row = page.getByRole("row").filter({ hasText: "内容加工" });
  await expect(row).toContainText("示例消息标题");
  await expect(row).toContainText("deepseek-v4.1-flash");
  await expect(row).toContainText("enrich-v2");
  await expect(row).toContainText("2 分 5 秒");
  // 失败原因独占一行，不挤窄其它列。
  await expect(
    page.getByRole("row").filter({ hasText: "任务快照的提示词" }),
  ).toContainText("任务快照的提示词已不存在，请重新创建任务");
  await expect(row.getByRole("link")).toHaveAttribute(
    "href",
    "/messages/f5a7c65d-c6b5-4373-a5c5-dd5b930c9bb9",
  );
  await expect(
    row.getByRole("button", { name: "重试任务", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("tasks-failed-1440.png"),
    fullPage: true,
  });
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 1440);
});
