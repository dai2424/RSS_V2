import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/**
 * 阶段 4 选题发现（P2b）验收：设置范围 → 触发发现 → 候选聚合与推荐理由 → 选题进入写作。
 *
 * 消息来自夹具 RSS（发布时间相对当前时刻生成，默认"最近 7 天"窗口一定覆盖；三条消息
 * 时间递增，因此前两条稳定地聚成一个事件）。模型来自夹具端点：把提示词里的消息按
 * "前两条一个事件、其余各自成候选"分组——形态与真实模型一致，服务端的越界过滤、
 * 去重与合并都照常参与。
 */

/** 每次调用都换一个后缀：来源 URL 唯一，避免同一份夹具被重复创建。 */
function uniqueSuffix() {
  return `discover-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

//: 本文件创建的来源与批次：用例结束后清理，避免污染共享的 e2e 库
//: （其它用例假设消息列表里以它们自己的数据为主，遗留的新消息会把它们的行挤出第一页）。
const created = { sources: [] as string[], batches: [] as string[] };

test.afterEach(async ({ request }) => {
  for (const batchId of created.batches.splice(0)) {
    await request.delete(`/api/discoveries/${batchId}`);
  }
  for (const sourceId of created.sources.splice(0)) {
    await request.delete(`/api/sources/${sourceId}`);
  }
});

/** 造一个来源三条消息并跑完采集；返回来源名。 */
async function seed(request: APIRequestContext) {
  const suffix = uniqueSuffix();
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "选题验收" + suffix,
        base_url: "http://127.0.0.1:8877/fixtures/v1",
      },
    })
  ).json();
  await request.post(`/api/llm/providers/${provider.id}/models`, {
    data: { model: "fixture-model" },
  });
  await request.post(`/api/llm/providers/${provider.id}/keys`, {
    data: { secret: "fixture-key" },
  });
  const sourceName = "选题验收来源" + suffix;
  const source = await (
    await request.post("/api/sources", {
      data: {
        name: sourceName,
        url: `http://127.0.0.1:8877/fixtures/stage4-feed.xml?case=${suffix}`,
        platform: "验收",
        language: "auto",
        category_id: await firstCategoryId(request),
      },
    })
  ).json();
  await request.post("/api/collection/runs", {
    data: { source_ids: [source.id] },
  });
  await drainWorker(request);
  return sourceName;
}

/** 选定来源并等到筛选写进 URL：与真实用户"选完看到已选中"的节奏一致。 */
async function pickSource(page: Page, sourceName: string) {
  await page.getByLabel("来源").selectOption({ label: sourceName });
  await expect(page).toHaveURL(/source=/);
  await expect(page.getByLabel("来源")).toHaveValue(/.+/);
}

/** 从详情页 URL 取出批次 id，供用例结束后清理。 */
function batchIdOf(page: Page) {
  const match = /\/discover\/([0-9a-f-]{36})/.exec(page.url());
  return match ? match[1] : "";
}

/** 页面无横向溢出：body 的 scrollWidth 等于视口宽度。 */
async function expectNoOverflow(page: Page, width: number) {
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
}

for (const width of [1280, 1920]) {
  test(`发现选题到选题建项目（${width}px）`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    const sourceName = await seed(request);

    // 1) 默认条件与实际生效范围
    await page.goto("/discover");
    await expect(page.getByRole("heading", { name: "发现选题" })).toBeVisible();
    await expect(page.getByLabel("时间范围")).toHaveValue("7d");
    await expect(page.getByLabel("分类")).toHaveValue("");
    await expect(page.getByLabel("来源")).toHaveValue("");
    await expect(
      page.getByText(/当前条件：最近 7 天 · 全部分类 · 全部来源/),
    ).toBeVisible();

    // 2) 限定来源后触发发现：点击只创建后台任务
    await pickSource(page, sourceName);
    await page.getByRole("button", { name: "发现选题" }).click();
    await expect(
      page.getByText("已创建发现任务，完成后会在这里列出候选。"),
    ).toBeVisible();
    created.batches.push(batchIdOf(page));

    // 3) worker 执行任务；页面轮询服务端状态，不需要手动刷新
    await drainWorker(request);
    await expect(
      page.getByText(/生效范围：.*消息 3 条 · 候选 2 个/),
    ).toBeVisible({
      timeout: 20000,
    });
    await expect(page.getByText("已完成").first()).toBeVisible();

    // 4) 候选：前两条聚成一个事件（附推荐理由），第三条单独成候选
    const first = page
      .locator("li.related-item", { hasText: "夹具事件 1" })
      .first();
    await expect(first.getByText("2 篇报道")).toBeVisible();
    await expect(first.getByText("夹具推荐理由 1")).toBeVisible();
    await expect(first.getByText("尊界 V800 制动测试引争议")).toBeVisible();
    await expect(first.getByText("尊界回应制动踏板问题")).toBeVisible();
    const single = page
      .locator("li.related-item", { hasText: "夹具事件 2" })
      .first();
    await expect(single.getByText("1 篇报道")).toBeVisible();
    await expect(
      single.getByText("开源项目让安卓车机跑 CarPlay"),
    ).toBeVisible();
    await expectNoOverflow(page, width);

    // 5) 选题进入写作项目；项目页可直接打开（刷新不 404），且没有不可用的后续入口
    await single.getByRole("button", { name: "写这篇" }).click();
    await expect(page.getByText("已选定事件，进入写作项目。")).toBeVisible();
    await expect(
      page.getByText(/状态：待准备资料 · 事件成员 1 篇/),
    ).toBeVisible();
    await expect(
      page.getByText(/资料准备、提纲确认与全文生成在后续任务中实现/),
    ).toBeVisible();
    const projectUrl = page.url();
    await page.reload();
    await expect(
      page.getByText(/状态：待准备资料 · 事件成员 1 篇/),
    ).toBeVisible();
    expect(page.url()).toBe(projectUrl);

    // 6) 返回选题：批次列表显示这次发现的范围、计数与状态
    await page.getByRole("link", { name: "返回选题" }).click();
    const row = page.locator("tr", { hasText: sourceName }).first();
    await expect(row.getByText("已完成")).toBeVisible();
    // 列顺序：范围 / 消息 / 候选 / 状态 / 创建时间 / 操作
    await expect(row.locator("td").nth(1)).toHaveText("3");
    await expect(row.locator("td").nth(2)).toHaveText("2");
    await expectNoOverflow(page, width);
  });
}

test("键盘可以完成发现与选题", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const sourceName = await seed(request);
  await page.goto("/discover");
  await pickSource(page, sourceName);
  // 触发与选择都只走键盘：范围与来源是原生 select（浏览器自带键盘行为）。
  const button = page.getByRole("button", { name: "发现选题" });
  await button.focus();
  await expect(button).toBeFocused();
  await page.keyboard.press("Enter");
  // 创建后先看到服务端的状态（排队/运行），再由 worker 执行、页面轮询到结果。
  created.batches.push(batchIdOf(page));
  await expect(page.getByText(/生效范围：/)).toBeVisible({ timeout: 20000 });
  await drainWorker(request);
  await expect(page.getByText(/候选 2 个/)).toBeVisible({ timeout: 20000 });
  const single = page
    .locator("li.related-item", { hasText: "夹具事件 2" })
    .first();
  const write = single.getByRole("button", { name: "写这篇" });
  await write.focus();
  await expect(write).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/状态：待准备资料 · 事件成员 1 篇/)).toBeVisible({
    timeout: 20000,
  });
});

/** 用可控响应验证失败状态与空态：不写库、不依赖模型，只看界面怎么呈现服务端结果。 */
test("批次失败与无候选时如实显示，不伪装成空选题", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const batchId = "11111111-1111-4111-8111-111111111111";
  const batch = {
    id: batchId,
    status: "failed",
    window_start: 1791337493,
    window_end: 1791942293,
    category_id: null,
    category_name: null,
    source_id: null,
    source_name: null,
    message_count: 89,
    candidate_count: 0,
    duplicate_members: 0,
    out_of_range: 0,
    dropped_members: 0,
    dropped_candidates: 0,
    error_code: "llm_output_truncated",
    error_message: "模型回答被输出长度上限截断，内容不完整",
    task_id: null,
    task_status: "failed",
    reused: false,
    created_at: 1791942293,
    updated_at: 1791942293,
  };
  await page.route("**/api/discoveries/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ batch, candidates: [] }),
    }),
  );
  await page.goto(`/discover/${batchId}`);
  await expect(page.getByRole("alert")).toContainText(
    "模型回答被输出长度上限截断，内容不完整",
  );
  await expect(page.getByText("失败").first()).toBeVisible();
  // 失败不等于"没有选题"：空态只在成功且没有候选时出现。
  await expect(page.getByText("这次没有值得写的选题")).toBeHidden();

  await page.route("**/api/discoveries/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        batch: {
          ...batch,
          status: "succeeded",
          task_status: "succeeded",
          error_code: null,
          error_message: null,
        },
        candidates: [],
      }),
    }),
  );
  await page.goto(`/discover/${batchId}`);
  await expect(page.getByText("这次没有值得写的选题")).toBeVisible();
  await expect(page.getByRole("link", { name: "调整范围" })).toBeVisible();
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 1440);
});
