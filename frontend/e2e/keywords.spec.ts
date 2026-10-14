import { test, expect, type APIRequestContext } from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/** 用接口造数据：一个来源、一次采集与加工，产出 fixture 关键词（计算平台、发布）。 */
async function seed(request: APIRequestContext, suffix: string) {
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "关键词验收" + suffix,
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
  const source = await (
    await request.post("/api/sources", {
      data: {
        name: "关键词来源" + suffix,
        url: "http://127.0.0.1:8877/fixtures/feed.xml?case=keywords" + suffix,
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
  // fixture 消息没到自动加工的长度阈值，这里显式触发一次内容加工。
  const page = await (await request.get("/api/messages?limit=50")).json();
  for (const message of page.items) {
    await request.post(`/api/messages/${message.id}/enrich`);
  }
  await drainWorker(request);
}

/** 把历史上遗留的合并撤销掉，保证用例可重复执行。 */
async function clearMerges(request: APIRequestContext) {
  const merges = await (
    await request.get("/api/keywords/merges?limit=50")
  ).json();
  for (const record of merges) {
    if (!record.undone_at) {
      await request.post(`/api/keywords/merges/${record.id}/undo`);
    }
  }
}

/** 清掉建议与批次台账：批次台账防重复生成，用例要重复执行就得先清。 */
async function clearSuggestions(request: APIRequestContext) {
  await request.post("/fixtures/suggestions/reset");
}

/** 取消历史上遗留的常驻标记；词表跨运行累积，否则"只看常驻"会带着旧行。 */
async function clearPins(request: APIRequestContext) {
  const pinned = await (
    await request.get("/api/keywords?pinned=true&min_count=1&limit=100")
  ).json();
  for (const item of pinned.items) {
    await request.post("/api/keywords/pin", {
      data: { key: item.key, pinned: false },
    });
  }
}

for (const width of [1280, 1920]) {
  test(`关键词统计、合并与撤销 ${width}px`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const suffix = String(Date.now());
    await clearMerges(request);
    await seed(request, suffix);

    await page.goto("/settings/keywords");
    await expect(
      page.getByRole("heading", { name: "关键词", exact: true }),
    ).toBeVisible({ timeout: 15000 });
    // 概览指标是真实数字：规范词数不少于 fixture 的两个词。
    const terms = page.locator(".keyword-metric", { hasText: "规范词数" });
    await expect(terms).toBeVisible();
    await expect(terms.locator("dd")).not.toHaveText("0");
    await expect(
      page.locator(".keyword-metric", { hasText: "已加工消息" }),
    ).toBeVisible();

    // 图表：aria 标注存在，线条颜色来自设计变量而不是默认黑。
    await expect(
      page.getByRole("img", { name: /每日词位数与新增词/ }),
    ).toBeVisible();
    // e2e 的 tsconfig 不含 DOM 库，这里用字符串表达式求值取计算样式。
    const stroke = String(
      await page.evaluate(
        "getComputedStyle(document.querySelector('.recharts-line-curve')).stroke",
      ),
    );
    expect(stroke).not.toBe("none");
    expect(stroke).not.toBe("rgb(0, 0, 0)");

    // 图表兜底数据表：展开后能看到与图一致的数字。
    await page
      .locator(".keyword-chart", { hasText: "每日产出" })
      .getByText("数据表", { exact: true })
      .click();
    await expect(
      page
        .locator(".keyword-chart", { hasText: "每日产出" })
        .getByRole("table"),
    ).toBeVisible();

    // 词表：默认折叠只出现一次的词，切到"含孤词"后用搜索定位 fixture 关键词。
    await page.getByLabel("最小出现次数").selectOption("1");
    await page.getByLabel("搜索关键词").fill("计算平台");
    const row = page.getByRole("row").filter({ hasText: "计算平台" }).first();
    await expect(row).toBeVisible();
    await page.getByLabel("搜索关键词").fill("");

    // 勾选两个词条：按可访问名定位，不用序号——列表刷新时行会移位。
    const box = (text: string) =>
      page.getByRole("checkbox", { name: `选择 ${text}` });
    await box("发布").check();
    await box("计算平台").check();
    await expect(box("发布")).toBeChecked();
    await expect(box("计算平台")).toBeChecked();
    await expect(page.getByText(/已选择 2 个词/)).toBeVisible();
    await page.getByRole("button", { name: "合并选中的词" }).click();

    const dialog = page.getByRole("dialog", { name: "合并关键词" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/将并入 1 个词条/)).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`keywords-merge-${width}.png`),
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "确认合并" }).focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeHidden();
    await expect(page.getByText("已合并，可在合并记录里撤销。")).toBeVisible();

    // 合并记录：成员写法出现在记录里，撤销后回到各自词条。
    const history = page.locator(".card", { hasText: "合并记录" });
    // 历史记录会跨运行累积（已撤销的也保留），这里只看最新一条。
    await expect(history.getByText(/←/).first()).toBeVisible();
    await history.getByRole("button", { name: "撤销" }).first().click();
    await expect(
      page.getByText("已撤销这次合并，写法回到各自词条。"),
    ).toBeVisible();
    await expect(history.getByText(/已于 .* 撤销/).first()).toBeVisible();

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });

  test(`常驻词标记与筛选 ${width}px`, async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await clearPins(request);
    await seed(request, `pins-${width}-${Date.now()}`);

    await page.goto("/settings/keywords");
    await expect(
      page.getByRole("heading", { name: "关键词", exact: true }),
    ).toBeVisible({ timeout: 15000 });
    await page.getByLabel("最小出现次数").selectOption("1");

    // 标记常驻：开关反映服务端状态，附带的文字也不只靠颜色表达。
    const toggle = page.getByRole("switch", { name: "常驻 计算平台" });
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await toggle.click();
    await expect(
      page.getByText("已标记常驻，注入提示词时必然出现。"),
    ).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(
      page.getByRole("row").filter({ hasText: "计算平台" }),
    ).toContainText("已常驻");

    // 只看常驻：只剩被标记的词，另一个 fixture 写法不再出现。
    await page.getByLabel("只看常驻").selectOption("1");
    await expect(
      page.getByRole("row").filter({ hasText: "计算平台" }),
    ).toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: "发布" })).toHaveCount(
      0,
    );
    await page.screenshot({
      path: testInfo.outputPath(`keywords-pinned-${width}.png`),
      fullPage: true,
    });

    // 收尾：取消标记，避免影响其他用例与下次运行。
    await page.getByRole("switch", { name: "常驻 计算平台" }).click();
    await expect(page.getByText("已取消常驻。")).toBeVisible();
    await expect(
      page.getByRole("row").filter({ hasText: "计算平台" }),
    ).toHaveCount(0);

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });

  test(`合并建议生成与采纳 ${width}px`, async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await clearMerges(request);
    await clearPins(request);
    await clearSuggestions(request);
    await seed(request, `suggest-${width}-${Date.now()}`);

    await page.goto("/settings/keywords");
    await expect(
      page.getByRole("heading", { name: "关键词", exact: true }),
    ).toBeVisible({ timeout: 15000 });

    const block = page.locator(".card", { hasText: "合并建议" });
    await expect(block.getByText("没有待确认的建议")).toBeVisible();
    await block.getByRole("button", { name: "生成一批建议" }).click();
    // 假端点会从注入的词表里挑两个写法组成一组，因此这里必然出现可采纳的候选。
    await expect(
      block.getByRole("button", { name: "采纳" }).first(),
    ).toBeVisible({
      timeout: 20000,
    });
    await expect(block.getByText(/本批 \d+ 个词/)).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`keywords-suggestions-${width}.png`),
      fullPage: true,
    });

    // 采纳：复用既有合并弹层，确认后合并记录里能看到这次合并。
    await block.getByRole("button", { name: "采纳" }).first().click();
    const dialog = page.getByRole("dialog", { name: "合并关键词" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/将并入 1 个词条/)).toBeVisible();
    await dialog.getByRole("button", { name: "确认合并" }).click();
    await expect(page.getByText("已合并，可在合并记录里撤销。")).toBeVisible();
    await expect(block.getByText("没有待确认的建议")).toBeVisible();

    // 收尾：撤销这次合并，保证用例可重复执行。
    const history = page.locator(".card", { hasText: "合并记录" });
    await history.getByRole("button", { name: "撤销" }).first().click();
    await expect(
      page.getByText("已撤销这次合并，写法回到各自词条。"),
    ).toBeVisible();

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });
}
