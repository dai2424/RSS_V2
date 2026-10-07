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
        RSS_LLM_KEY_E2E: "fake-e2e-secret",
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
    await page
      .getByRole("link", { name: "New computing platform", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "New computing platform" }).first(),
    ).toBeVisible();
    const messageUrl = page.url();
    const providers = await (await request.get("/api/llm/providers")).json();
    if (!providers.length) {
      const provider = await (
        await request.post("/api/llm/providers", {
          data: {
            name: "验收 Provider",
            base_url: "http://127.0.0.1:8877/fixtures/v1",
            model: "e2e-model",
          },
        })
      ).json();
      await request.post(`/api/llm/providers/${provider.id}/keys`, {
        data: { key_ref: "E2E" },
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
    await page.screenshot({
      path: testInfo.outputPath(`message-${width}.png`),
      fullPage: true,
    });
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
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

test("Provider 与 Key 引用配置，1280px 桌面可操作", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 1000 });
  const name = "界面配置验收" + Date.now();
  await page.goto("/settings/providers");
  const create = page.locator("form").first();
  await create.getByLabel("名称", { exact: true }).fill(name);
  await create
    .getByLabel("兼容 API Base URL", { exact: true })
    .fill("http://127.0.0.1:8877/fixtures/v1");
  await create.getByLabel("模型", { exact: true }).fill("ui-test-model");
  await create
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  const card = page.getByRole("region", { name, exact: true });
  await expect(card.getByRole("heading", { name, exact: true })).toBeVisible();
  await card.getByLabel("Key 引用名", { exact: true }).fill("UI_" + Date.now());
  await card.getByRole("button", { name: "添加引用", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "停用 Key", exact: true }),
  ).toBeVisible();
  await card.getByRole("button", { name: "停用 Key", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "启用 Key", exact: true }),
  ).toBeVisible();
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  await card.getByLabel("模型", { exact: true }).fill("ui-model-updated");
  await card.getByRole("button", { name: "关闭编辑", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(card.getByLabel("模型", { exact: true })).toHaveValue(
    "ui-model-updated",
  );
  await card.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(card.getByText(/ui-model-updated/).first()).toBeVisible();
  await card.getByRole("button", { name: "停用", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "启用", exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 1280);
  await page.screenshot({
    path: testInfo.outputPath("providers-1280.png"),
    fullPage: true,
  });
});

test("Provider 保存失败保留输入并保护离开", async ({ page }) => {
  await page.goto("/settings/providers");
  const form = page.locator("form").first();
  await form.getByLabel("名称", { exact: true }).fill("待保存配置");
  await form
    .getByLabel("兼容 API Base URL", { exact: true })
    .fill("https://example.test/v1");
  await form.getByLabel("模型", { exact: true }).fill("test-model");
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
  await form
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  await expect(form.getByRole("alert")).toContainText("配置保存暂不可用");
  await expect(form.getByLabel("名称", { exact: true })).toHaveValue(
    "待保存配置",
  );
  await page.getByRole("link", { name: "RSS 来源", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(form.getByLabel("模型", { exact: true })).toHaveValue(
    "test-model",
  );
  await page.getByRole("link", { name: "RSS 来源", exact: true }).click();
  await page
    .getByRole("button", { name: "放弃修改并离开", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "RSS 来源", exact: true }),
  ).toBeVisible();
});
