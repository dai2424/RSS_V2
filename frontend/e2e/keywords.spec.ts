import { test, expect, type APIRequestContext } from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/** 用接口造数据：一个来源、一次采集与加工，产出 fixture 关键词（计算平台、发布）。 */
async function seed(
  request: APIRequestContext,
  suffix: string,
  fixture = "feed.xml",
) {
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
  const sourceName = "关键词来源" + suffix;
  const source = await (
    await request.post("/api/sources", {
      data: {
        name: sourceName,
        url: `http://127.0.0.1:8877/fixtures/${fixture}?case=keywords` + suffix,
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
  return sourceName;
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

/** 清掉建议、批次台账与遗留的已结束任务：用例要重复执行就得先清。 */
async function clearSuggestions(request: APIRequestContext) {
  await request.post("/fixtures/suggestions/reset");
  // 任务用产品自带的任务清理入口：已结束的批次任务留着会让"已排过"挡住重排。
  await request.post("/api/tasks/clear", {
    data: { statuses: ["succeeded", "failed"], dry_run: false },
  });
}

/** 取消历史上遗留的常驻标记；词表跨运行累积，否则"只看常驻"会带着旧行。 */
async function clearPins(request: APIRequestContext) {
  const pinned = await (
    await request.get(
      "/api/keywords?pinned=true&scope=all&min_count=1&limit=100",
    )
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
    const terms = page.locator(".keyword-metric", { hasText: "存量词表" });
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
    await page.getByLabel("口径").selectOption("all");
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
    await page.getByLabel("口径").selectOption("all");
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

    // 清理词表状态：入口在页面右上角的维护区，先预览再执行。
    await page.getByRole("button", { name: "清理词表状态" }).click();
    const purgeDialog = page.getByRole("dialog", { name: "清理词表状态" });
    await expect(purgeDialog).toBeVisible();
    await expect(purgeDialog.getByText(/^预览：扫描记账/)).toBeVisible();
    await purgeDialog.getByRole("button", { name: "取消" }).click();
    await expect(purgeDialog).toBeHidden();

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

    // 点一次只排一个批次：任务由独立 worker 执行，进度写在区块里。
    await block.getByRole("button", { name: "扫一批" }).click();
    await expect(page.getByText(/已排入 1 个批次/)).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`keywords-suggestions-${width}.png`),
      fullPage: true,
    });
    await drainWorker(request);
    // 假端点会从注入的词表里挑两个写法组成一组，等轮询把它带出来。
    await expect(
      block.getByRole("button", { name: "采纳" }).first(),
    ).toBeVisible({
      timeout: 20000,
    });
    await expect(block.getByText(/新词 \d+ 个/)).toBeVisible();

    // 采纳：复用既有合并弹层，确认后合并记录里能看到这次合并。
    await block.getByRole("button", { name: "采纳" }).first().click();
    const dialog = page.getByRole("dialog", { name: "合并关键词" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/将并入 1 个词条/)).toBeVisible();
    await dialog.getByRole("button", { name: "确认合并" }).click();
    await expect(page.getByText("已合并，可在合并记录里撤销。")).toBeVisible();
    await expect(block.getByText("没有待确认的建议")).toBeVisible();

    // 常驻建议：夹具没有跨周历史，因此候选为空——界面必须明说样本不够，而不是硬凑。
    const pinBlock = page.locator(".card", { hasText: "常驻建议" });
    await expect(pinBlock.getByText("没有待确认的常驻建议")).toBeVisible();
    await pinBlock.getByRole("button", { name: "推荐常驻词" }).click();
    await expect(page.getByText(/样本还不够/)).toBeVisible();

    // 任务列表里能看到这次批次：类型、目标（词表区间）与提示词版本。
    await page.getByRole("link", { name: "任务", exact: true }).first().click();
    const taskRow = page
      .getByRole("row")
      .filter({ hasText: "合并建议" })
      .first();
    await expect(taskRow).toContainText("词表第");
    await expect(taskRow).toContainText("merge-suggest-v");
    await page.screenshot({
      path: testInfo.outputPath(`keywords-suggestion-task-${width}.png`),
      fullPage: true,
    });

    // 收尾：撤销这次合并，保证用例可重复执行。
    await page.goto("/settings/keywords");
    const history = page.locator(".card", { hasText: "合并记录" });
    await history.getByRole("button", { name: "撤销" }).first().click();
    await expect(
      page.getByText("已撤销这次合并，写法回到各自词条。"),
    ).toBeVisible();

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });

  test(`词表按关键词查看消息 ${width}px`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const suffix = `jump-${width}-${Date.now()}`;
    const sourceName = await seed(request, suffix, "two-items-feed.xml");

    // 从词表进入：默认热词口径折叠了只出现一两次的词，先切到全量。
    await page.goto("/settings/keywords");
    await expect(
      page.getByRole("heading", { name: "关键词", exact: true }),
    ).toBeVisible({ timeout: 15000 });
    await page.getByLabel("口径").selectOption("all");
    await page.getByLabel("最小出现次数").selectOption("1");
    await page.getByLabel("搜索关键词").fill("计算平台");
    const row = page.getByRole("row").filter({ hasText: "计算平台" }).first();
    await expect(row).toBeVisible();
    await row.getByRole("link", { name: "查看消息" }).click();

    // 消息页说清是按哪个关键词收窄的，并且用的是结构化关键词而不是全文搜索。
    await expect(page).toHaveURL(/keyword=/);
    await expect(page.getByText("关键词：计算平台")).toBeVisible();
    await page.getByLabel("来源筛选").selectOption({ label: sourceName });
    const second = page.getByRole("row").filter({ hasText: "第二平台更新" });
    // 第二条消息的正文里出现"计算平台"，但它的结构化关键词是别的词：不该被命中。
    await expect(second).toHaveCount(0);
    await expect(
      page.getByRole("row").filter({ hasText: "精简标题" }).first(),
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`messages-keyword-${width}.png`),
      fullPage: true,
    });

    // 清除关键词条件后，被排除的那条消息重新出现。
    await page.getByRole("button", { name: "清除关键词" }).click();
    await expect(page.getByText("关键词：计算平台")).toBeHidden();
    await expect(second).toHaveCount(1);

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });
}
