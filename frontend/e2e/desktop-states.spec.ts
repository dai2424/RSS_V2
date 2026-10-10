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
      body: JSON.stringify({ items: [failed], total: 1 }),
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
  // 分页条：总数、每页条数、页码直选和跳页都在一条里，当前页有 aria-current。
  const pager = page.getByRole("navigation", { name: "分页" });
  await expect(pager).toContainText("共 1 个任务");
  await expect(pager.getByLabel("每页条数")).toHaveValue("25");
  await expect(pager.getByText("1", { exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await page.screenshot({
    path: testInfo.outputPath("tasks-failed-1440.png"),
    fullPage: true,
  });
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 1440);
});

test("任务删除入口：未结束禁用，清理失败任务先预览", async ({ page }) => {
  const base = {
    idempotency_key: "key",
    lease_until: null,
    input_version_id: null,
    output_version_id: null,
    error_code: null,
    error_message: null,
    model: "deepseek-v4.1-flash",
    prompt_version: "enrich-v2",
    target_kind: "message",
    target_id: "m-1",
    target_label: "示例消息",
  };
  await page.route("**/api/tasks?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            ...base,
            id: "queued-task",
            task_type: "enrich_message",
            status: "queued",
            attempts: 0,
            created_at: 1759999000,
            updated_at: 1759999000,
          },
          {
            ...base,
            id: "failed-task",
            task_type: "enrich_message",
            status: "failed",
            attempts: 2,
            created_at: 1759998000,
            updated_at: 1759998125,
          },
        ],
        total: 2,
      }),
    }),
  );
  const clearBodies: unknown[] = [];
  await page.route("**/api/tasks/clear-failed", async (route) => {
    const body = route.request().postDataJSON();
    clearBodies.push(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        body.dry_run
          ? { candidates: 1, deleted: 0 }
          : { candidates: 1, deleted: 1 },
      ),
    });
  });

  await page.goto("/tasks");
  // 排队中的任务不能删：按钮禁用并说明原因。
  const queuedRow = page.getByRole("row").filter({ hasText: "已排队" });
  const queuedDelete = queuedRow.getByRole("button", { name: "删除" });
  await expect(queuedDelete).toBeDisabled();
  await expect(queuedDelete).toHaveAttribute(
    "title",
    "排队中或运行中的任务不能删除",
  );

  // 失败任务可以删：确认弹层说明结果与审计保留，Esc 可退出。
  const failedRow = page.getByRole("row").filter({ hasText: "失败" }).first();
  await failedRow.getByRole("button", { name: "删除" }).click();
  const dialog = page.getByRole("alertdialog", { name: "删除任务" });
  await expect(dialog).toContainText("模型调用审计");
  await expect(dialog).toContainText("不可恢复");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  // 清理失败任务：先预览条数，再按条数确认。
  await page.getByRole("button", { name: "清理失败任务" }).click();
  const clearDialog = page.getByRole("alertdialog", { name: "清理失败任务" });
  await expect(clearDialog).toContainText("将删除 1 个失败任务");
  await clearDialog.getByRole("button", { name: "清理 1 个任务" }).click();
  await expect(page.getByText("已清理 1 个失败任务。")).toBeVisible();
  expect(clearBodies).toEqual([{ dry_run: true }, { dry_run: false }]);
});

test("筛选控件：消息处理状态与任务筛选都写进查询参数", async ({ page }) => {
  const messageUrls: string[] = [];
  await page.route("**/api/messages?**", async (route) => {
    messageUrls.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0 }),
    });
  });
  await page.goto("/messages");
  await page.getByLabel("处理状态").selectOption("unenriched");
  await expect(page.getByLabel("处理状态")).toHaveValue("unenriched");
  await expect
    .poll(() => messageUrls.some((url) => url.includes("state=unenriched")))
    .toBe(true);

  const taskUrls: string[] = [];
  await page.route("**/api/tasks?**", async (route) => {
    taskUrls.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0 }),
    });
  });
  await page.goto("/tasks");
  await page.getByLabel("类型").selectOption("enrich_message");
  await page.getByLabel("时间范围").selectOption("7d");
  await page.getByLabel("搜索任务").fill("华为");
  await expect
    .poll(() =>
      taskUrls.some((url) => url.includes("task_type=enrich_message")),
    )
    .toBe(true);
  await expect
    .poll(() =>
      taskUrls.some((url) => url.includes("since=") && url.includes("q=")),
    )
    .toBe(true);
});
