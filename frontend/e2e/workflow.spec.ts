import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import type { APIRequestContext } from "@playwright/test";

function runWorkerOnce() {
  execFileSync(
    "uv",
    ["run", "python", "-m", "rss_v2.main", "worker", "--once"],
    {
      cwd: resolve(".."),
      env: {
        ...process.env,
        RSS_RUNTIME_DIR: resolve("../runtime/e2e"),
      },
      stdio: "pipe",
    },
  );
}

/** 反复执行 worker 直到队列排空，避免历史滞留任务抢占单次执行机会。 */
async function drainWorker(request: APIRequestContext) {
  for (let i = 0; i < 20; i += 1) {
    runWorkerOnce();
    const queued = await (await request.get("/api/tasks?status=queued")).json();
    if (!queued.length) return;
  }
  throw new Error("任务队列未能排空，可能存在反复失败的任务。");
}

for (const width of [1280, 1440, 1920]) {
  test(`RSS 主流程与键盘操作 ${width}px`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const suffix = String(Date.now());
    await page.goto("/sources");
    await page.getByRole("button", { name: "新增行业", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "新增行业" });
    await dialog.getByLabel("行业名称").fill("浏览器验收" + suffix);
    await dialog.getByRole("button", { name: "新增", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByRole("option", { name: "浏览器验收" + suffix }),
    ).toBeAttached();
    await page.getByRole("link", { name: "新增来源", exact: true }).click();
    const categorySelect = page.getByLabel("行业分类", { exact: true });
    await page.getByRole("button", { name: "新增行业", exact: true }).click();
    const formDialog = page.getByRole("dialog", { name: "新增行业" });
    await formDialog.getByLabel("行业名称").fill("表单验收" + suffix);
    await formDialog.getByRole("button", { name: "新增", exact: true }).click();
    await expect(formDialog).toBeHidden();
    await expect(categorySelect.locator("option:checked")).toHaveText(
      "表单验收" + suffix,
    );
    await page
      .getByLabel("来源名称", { exact: true })
      .fill("测试来源" + suffix);
    await page
      .getByLabel("RSS 地址", { exact: true })
      .fill("http://127.0.0.1:8877/fixtures/feed.xml?case=" + suffix);
    await page
      .getByLabel("行业分类", { exact: true })
      .selectOption({ label: "浏览器验收" + suffix });
    await page.getByRole("button", { name: "保存来源", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "测试来源" + suffix }),
    ).toBeVisible();
    await page.getByRole("link", { name: "编辑", exact: true }).click();
    await expect(page.getByLabel("来源名称", { exact: true })).toHaveValue(
      "测试来源" + suffix,
    );
    await page.getByLabel("平台", { exact: true }).fill("浏览器验收平台");
    await page.getByRole("button", { name: "保存来源", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "测试来源" + suffix }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "测试来源" + suffix }),
    ).toBeVisible();
    await page.getByRole("button", { name: "测试来源", exact: true }).click();
    await expect(
      page.getByText("解析成功", { exact: true }).first(),
    ).toBeVisible();
    await page.getByRole("button", { name: "立即采集", exact: true }).click();
    await expect(
      page.getByText("已创建采集任务", { exact: false }),
    ).toBeVisible();
    await drainWorker(request);
    await page
      .getByRole("link", { name: "查看本来源消息", exact: true })
      .click();
    // 采集后可能已自动生成中文，列表标题会变，因此按第一行数据取消息。
    await page.locator("tbody tr").first().getByRole("link").first().click();
    await expect(
      page.getByRole("heading", { name: "New computing platform" }).first(),
    ).toBeVisible();
    const messageUrl = page.url();
    // 保证存在至少一个可用的启用模型：没有就新建，避免改到已停用的历史 Provider。
    const providers = await (await request.get("/api/llm/providers")).json();
    const usable = providers.find(
      (item: {
        enabled: boolean;
        keys: { enabled: boolean }[];
        models: { enabled: boolean }[];
      }) =>
        item.enabled &&
        item.keys.some((key) => key.enabled) &&
        item.models.some((model) => model.enabled),
    );
    if (!usable) {
      const provider = await (
        await request.post("/api/llm/providers", {
          data: {
            name: "验收 Provider " + suffix,
            base_url: "http://127.0.0.1:8877/fixtures/v1",
          },
        })
      ).json();
      await request.post(`/api/llm/providers/${provider.id}/models`, {
        data: { model: "e2e-model" },
      });
      await request.post(`/api/llm/providers/${provider.id}/keys`, {
        data: { secret: "fake-e2e-secret" },
      });
    }
    await page.getByRole("button", { name: "生成中文", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "翻译任务" }),
    ).toBeVisible();
    await drainWorker(request);
    await page
      .getByRole("tab", { name: "中文版本（机器生成）", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "新计算平台", exact: true }),
    ).toBeVisible({ timeout: 15000 });
    // 内容加工：手动生成摘要后，摘要标签页展示精简标题与关键词。
    await page.getByRole("button", { name: "生成摘要", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "加工任务" }),
    ).toBeVisible();
    await drainWorker(request);
    await page
      .getByRole("tab", { name: "内容摘要（机器生成）", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "精简标题", exact: true }),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByRole("tabpanel").getByText("计算平台", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`message-${width}.png`),
      fullPage: true,
    });
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    // 中文关键词能命中机器生成的摘要与关键词，而不只是原文。
    await page.goto("/messages");
    await page.getByLabel("搜索消息").fill("检索归档");
    await expect(
      page.getByRole("row").filter({ hasText: "精简标题" }).first(),
    ).toBeVisible();
    const sidebar = page.getByRole("complementary", { name: "主导航" });
    await expect(sidebar).toBeVisible();
    await sidebar.getByRole("link", { name: "任务", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "任务", exact: true }),
    ).toBeVisible();
    await page.goto("/sources");
    const row = page.getByRole("row").filter({ hasText: "测试来源" + suffix });
    await expect(page.getByText(/共 \d+ 条 · 第 1 页/)).toBeVisible();
    await row.getByRole("switch").click();
    await expect(row).toContainText("已停用");
    await page.goto(messageUrl);
    await expect(
      page.getByRole("heading", { name: "New computing platform" }).first(),
    ).toBeVisible();
    expect(errors).toEqual([]);
    await page.goto("/sources");
    await expect(
      page.getByRole("heading", { name: "RSS 来源", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    await page.screenshot({
      path: testInfo.outputPath(`sources-${width}.png`),
      fullPage: true,
    });
    // 删除来源：确认弹层说明影响后执行，行与消息一并清理。
    await row.getByRole("button", { name: "删除", exact: true }).click();
    const confirm = page.getByRole("alertdialog", { name: "删除来源" });
    await expect(confirm).toContainText("不可恢复");
    await expect(confirm).toContainText("1 条消息");
    await confirm
      .getByRole("button", { name: "确认删除", exact: true })
      .click();
    await expect(confirm).toBeHidden();
    await expect(
      page.getByRole("row").filter({ hasText: "测试来源" + suffix }),
    ).toHaveCount(0);
  });
}

test("保存失败保留表单与未保存离开提示", async ({ page }) => {
  await page.goto("/sources/new");
  await page.getByRole("button", { name: "保存来源", exact: true }).click();
  await expect(page.getByText("请输入来源名称", { exact: true })).toBeVisible();
  await expect(page.getByText("请选择行业分类", { exact: true })).toBeVisible();
  await page.getByLabel("来源名称", { exact: true }).fill("保留输入");
  await page
    .getByLabel("RSS 地址", { exact: true })
    .fill("http://127.0.0.1:8877/fixtures/feed.xml?failure=true");
  await page.getByLabel("行业分类", { exact: true }).selectOption({ index: 1 });
  let finish = () => {};
  const submitted = new Promise<void>((resolve) => {
    finish = resolve;
  });
  await page.route("**/api/sources", async (route) => {
    await submitted;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ code: "unavailable", message: "服务暂不可用" }),
    });
  });
  try {
    await page.getByRole("button", { name: "保存来源", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "保存中…", exact: true }),
    ).toBeDisabled();
  } finally {
    finish();
  }
  await expect(page.getByRole("alert")).toContainText("服务暂不可用");
  await expect(page.getByLabel("来源名称", { exact: true })).toHaveValue(
    "保留输入",
  );
  await page.getByRole("link", { name: "取消", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "继续编辑", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("alertdialog").locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Shift+Tab");
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "取消", exact: true }),
  ).toBeFocused();
  await expect(page.getByLabel("来源名称", { exact: true })).toHaveValue(
    "保留输入",
  );
});

test("Provider、模型与 API Key 配置，1280px 桌面可操作", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  const name = "界面配置验收" + Date.now();
  await page.goto("/settings/providers");
  await page
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  const createDialog = page.getByRole("dialog", { name: "添加 Provider" });
  await createDialog.getByLabel("名称", { exact: true }).fill(name);
  await createDialog
    .getByLabel("兼容 API Base URL", { exact: true })
    .fill("http://127.0.0.1:8877/fixtures/v1");
  await createDialog
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  await expect(createDialog).toBeHidden();
  const card = page.getByRole("region", { name, exact: true });
  await expect(card.getByRole("heading", { name, exact: true })).toBeVisible();
  // 新建供应商自动选中；左侧列表行含状态文字。
  const listItem = page.getByRole("button").filter({ hasText: name }).first();
  await expect(listItem).toHaveAttribute("aria-current", "true");
  await expect(
    page.getByText("未配置 Key", { exact: true }).first(),
  ).toBeVisible();

  // API Key：弹层录入，列表只显示末四位掩码；可编辑替换与启停。
  await card.getByRole("button", { name: "添加 Key", exact: true }).click();
  const keyDialog = page.getByRole("dialog", { name: "添加 API Key" });
  await keyDialog
    .getByLabel("API Key", { exact: true })
    .fill("ui-key-9999-abcd");
  await keyDialog
    .getByRole("button", { name: "显示 API Key", exact: true })
    .click();
  await expect(
    keyDialog.getByLabel("API Key", { exact: true }),
  ).toHaveAttribute("type", "text");
  await keyDialog
    .getByRole("button", { name: "隐藏 API Key", exact: true })
    .click();
  await keyDialog.getByRole("button", { name: "添加", exact: true }).click();
  await expect(keyDialog).toBeHidden();
  await expect(card.getByText("••••••••abcd", { exact: true })).toBeVisible();
  await expect(card.getByText("ui-key-9999-abcd")).toHaveCount(0);
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  const keyEdit = page.getByRole("dialog", { name: "编辑 API Key" });
  await keyEdit
    .getByLabel("新 API Key（可选）", { exact: true })
    .fill("ui-key-8888-wxyz");
  await keyEdit.getByRole("button", { name: "保存", exact: true }).click();
  await expect(keyEdit).toBeHidden();
  await expect(card.getByText("••••••••wxyz", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "停用 Key", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "启用 Key", exact: true }),
  ).toBeVisible();
  // 恢复启用，供后续测试连接使用。
  await card.getByRole("button", { name: "启用 Key", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "停用 Key", exact: true }),
  ).toBeVisible();

  // 模型：弹层添加 → 行内测试连接 → 行内启停 → 编辑弹层带未保存确认。
  await card.getByRole("button", { name: "添加模型", exact: true }).click();
  const modelAdd = page.getByRole("dialog", { name: "添加模型" });
  await modelAdd.getByLabel("模型 ID", { exact: true }).fill("ui-test-model");
  await modelAdd.getByRole("button", { name: "添加", exact: true }).click();
  await expect(modelAdd).toBeHidden();
  const modelRow = card
    .locator(".provider-row")
    .filter({ hasText: "ui-test-model" });
  await expect(modelRow).toBeVisible();
  // 固定 fixture 返回合法结构化翻译，测试连接应成功并显示延迟。
  await modelRow.getByRole("button", { name: "测试", exact: true }).click();
  // 假端点按协议给出不同用量：3 tokens 说明走的是 chat completions。
  await expect(
    card.getByRole("status").filter({ hasText: "连接成功" }),
  ).toContainText("3 tokens");
  // 切到 Anthropic Messages 协议：详情行同步显示，连接测试改走 /messages 假端点。
  await card.getByRole("button", { name: "编辑连接", exact: true }).click();
  const connectionDialog = page.getByRole("dialog", { name: "编辑连接配置" });
  await connectionDialog
    .getByLabel("API 格式", { exact: true })
    .selectOption({ label: "Anthropic Messages（/messages）" });
  await connectionDialog
    .getByRole("button", { name: "保存修改", exact: true })
    .click();
  await expect(connectionDialog).toBeHidden();
  // 列表刷新有轮询间隔，这里等协议行落到详情里。
  await expect(card).toContainText("Anthropic Messages（/messages）", {
    timeout: 15000,
  });
  await card.getByRole("button", { name: "测试连接", exact: true }).click();
  // 换成 Messages 形状的假端点后用量变成 5+3，据此确认请求打到了 /messages。
  await expect(
    card.getByRole("status").filter({ hasText: "连接成功" }),
  ).toContainText("8 tokens");
  await modelRow
    .getByRole("switch", { name: "启用模型 ui-test-model" })
    .click();
  await expect(modelRow.getByText("已停用", { exact: true })).toBeVisible();
  await modelRow.getByRole("button", { name: "编辑", exact: true }).click();
  const modelDialog = page.getByRole("dialog", { name: "编辑模型" });
  await modelDialog
    .getByLabel("模型 ID", { exact: true })
    .fill("ui-model-updated");
  await modelDialog.getByRole("button", { name: "关闭", exact: true }).click();
  const confirmLeave = page.getByRole("alertdialog", { name: "未保存的修改" });
  await expect(confirmLeave).toBeVisible();
  // 等焦点进入确认弹层再按 Escape：焦点陷阱未就绪时按键会丢失，导致偶发失败。
  await expect(
    confirmLeave.getByRole("button", { name: "继续编辑", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(modelDialog.getByLabel("模型 ID", { exact: true })).toHaveValue(
    "ui-model-updated",
  );
  await modelDialog
    .getByRole("button", { name: "保存修改", exact: true })
    .click();
  await expect(card.getByText(/ui-model-updated/).first()).toBeVisible();

  // 供应商启停开关与状态文字。
  await card.getByRole("switch", { name: `启用供应商 ${name}` }).click();
  await expect(card.getByText("已停用", { exact: true }).first()).toBeVisible();
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 1280);
  await page.screenshot({
    path: testInfo.outputPath("providers-1280.png"),
    fullPage: true,
  });
});

test("Provider 保存失败保留输入并保护离开", async ({ page }) => {
  await page.goto("/settings/providers");
  await page
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "添加 Provider" });
  await dialog.getByLabel("名称", { exact: true }).fill("待保存配置");
  await dialog
    .getByLabel("兼容 API Base URL", { exact: true })
    .fill("https://example.test/v1");
  await page.route("**/api/llm/providers", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            code: "unavailable",
            message: "配置保存暂不可用",
          }),
        })
      : route.continue(),
  );
  await dialog
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("配置保存暂不可用");
  await expect(dialog.getByLabel("名称", { exact: true })).toHaveValue(
    "待保存配置",
  );
  // 弹层内关闭需确认；继续编辑后输入仍在。
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(dialog.getByLabel("名称", { exact: true })).toHaveValue(
    "待保存配置",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "放弃修改并离开", exact: true })
    .click();
  await expect(dialog).toBeHidden();
  // “添加 Key”弹层同样保护未提交密钥：关闭需确认，放弃后清空。
  await page.getByRole("button", { name: "添加 Key", exact: true }).click();
  const keyDialog = page.getByRole("dialog", { name: "添加 API Key" });
  await keyDialog
    .getByLabel("API Key", { exact: true })
    .fill("discard-me-1234");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(keyDialog.getByLabel("API Key", { exact: true })).toHaveValue(
    "discard-me-1234",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "放弃修改并离开", exact: true })
    .click();
  await expect(keyDialog).toBeHidden();
  await page.getByRole("button", { name: "添加 Key", exact: true }).click();
  await expect(keyDialog.getByLabel("API Key", { exact: true })).toHaveValue(
    "",
  );
  await keyDialog.getByRole("button", { name: "关闭", exact: true }).click();
});

for (const width of [1280, 1440, 1920]) {
  test(`提示词新建、就地编辑、启用与删除 ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const suffix = String(Date.now());
    const key = `e2e-brief-${suffix}`;
    const version = `${key}-v1`;
    await page.goto("/settings/prompts");

    // 新建：长正文在页面内编辑，故意漏声明输出字段，断言编译错误就地显示。
    await page.getByRole("button", { name: "新建提示词", exact: true }).click();
    await page
      .getByLabel("任务类型", { exact: true })
      .selectOption({ label: "内容加工" });
    await page.getByLabel("提示词标识", { exact: true }).fill(key);
    await page
      .getByLabel("版本名称", { exact: true })
      .fill("验收草稿" + suffix);
    await page
      .getByLabel("用户提示", { exact: true })
      .fill("只输出摘要：{{title}}");
    await page.getByRole("button", { name: "编译预览", exact: true }).click();
    await expect(page.locator(".alert-error")).toContainText("编译未通过");
    await expect(page.locator(".alert-error")).toContainText("keywords");
    await page.screenshot({
      path: testInfo.outputPath(`prompts-create-${width}.png`),
      fullPage: true,
    });

    // 修正后创建：新版本进入列表，并说明它还没被引用过。
    await page
      .getByLabel("用户提示", { exact: true })
      .fill("输出 title、summary、keywords：{{title}} {{summary}} {{content}}");
    await page.getByRole("button", { name: "创建提示词", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: version, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/该版本还没有被任何调用、结果、任务或分配引用/),
    ).toBeVisible();

    // 就地编辑：未被引用的草稿可以直接改文本，版本串不变。
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page
      .getByLabel("版本名称", { exact: true })
      .fill("改过的验收草稿" + suffix);
    await page.getByLabel("版本说明", { exact: true }).fill("就地改了文本");
    await page.getByRole("button", { name: "保存修改", exact: true }).click();
    await expect(page.getByText("改过的验收草稿" + suffix)).toBeVisible();
    await expect(page.getByText("版本说明：就地改了文本")).toBeVisible();

    // 启用后不能删：按钮禁用，并用文字说明原因。
    await page.getByRole("button", { name: "启用此版本", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "当前启用版本", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "删除", exact: true }),
    ).toBeDisabled();
    await expect(page.getByText(/它正在启用/)).toBeVisible();

    // 切回内置版本（新版本随之归档），恢复全局启用状态。
    await page.getByRole("button", { name: "enrich-v1" }).click();
    await expect(
      page.getByRole("heading", { name: "enrich-v1", exact: true }),
    ).toBeVisible();
    const activate = page.getByRole("button", {
      name: "启用此版本",
      exact: true,
    });
    if ((await activate.count()) > 0) {
      await activate.click();
    }
    await expect(
      page.getByRole("button", { name: "当前启用版本", exact: true }),
    ).toBeVisible();

    // 删除已归档且未被引用的版本：确认弹层说明对象与影响后从列表消失。
    await page.getByRole("button", { name: version }).click();
    await expect(
      page.getByRole("heading", { name: version, exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "删除", exact: true }).click();
    const confirm = page.getByRole("alertdialog", { name: "删除提示词版本" });
    await expect(confirm).toContainText(version);
    await expect(confirm).toContainText("不可恢复");
    await confirm
      .getByRole("button", { name: "确认删除", exact: true })
      .click();
    await expect(confirm).toBeHidden();
    await expect(page.getByRole("button", { name: version })).toHaveCount(0);
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    await page.screenshot({
      path: testInfo.outputPath(`prompts-${width}.png`),
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}
