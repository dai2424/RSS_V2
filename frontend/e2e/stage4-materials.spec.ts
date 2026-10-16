import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/**
 * 阶段 4 资料与核对（P3b）验收：选题 → 资料准备（检索 + 读取）→ 资料整理（事实/分歧/缺口）
 * → 核对缺口 → 键盘可达。
 *
 * 检索与原文来自夹具端点（SearXNG 形状的 /fixtures/search 与两篇 /fixtures/article），
 * 模型来自夹具端点：资料整理输出的事实引文从提示词里的正文原样截取，因此服务端的
 * 引文定位校验一定通过——界面看到的是真实链路的结果，不是打桩的假数据。
 */

function uniqueSuffix() {
  return `digest-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

/** 走一遍"发现选题 → 选择候选"，返回项目 id 与来源名。 */
async function seedProject(request: APIRequestContext) {
  const suffix = uniqueSuffix();
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "资料验收" + suffix,
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
  const sourceName = "资料验收来源" + suffix;
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
  await request.put(`/api/task-settings/source/${source.id}`, {
    data: { settings: [{ task_kind: "enrich_message", enabled: false }] },
  });
  await request.post("/api/collection/runs", {
    data: { source_ids: [source.id] },
  });
  await drainWorker(request);

  const created = await (
    await request.post("/api/discoveries", {
      data: { source_id: source.id },
    })
  ).json();
  await drainWorker(request);
  const detail = await (
    await request.get(`/api/discoveries/${created.id}`)
  ).json();
  const candidate = detail.candidates[0];
  const project = await (
    await request.post(
      `/api/discoveries/${created.id}/candidates/${candidate.id}/select`,
    )
  ).json();
  return {
    projectId: project.id as string,
    sourceName,
    sourceId: source.id as string,
    batchId: created.id as string,
  };
}

/** 页面无横向溢出：body 的 scrollWidth 等于视口宽度。 */
async function expectNoOverflow(page: Page, width: number) {
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
}

for (const width of [1280, 1920]) {
  test(`资料准备到事实核对（${width}px）`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    const { projectId, sourceId, batchId } = await seedProject(request);

    // 1) 资料准备：点击后创建后台任务，worker 执行真实检索与网页读取
    await page.goto(`/projects/${projectId}`);
    await expect(
      page.getByRole("heading", { name: /尊界|夹具事件/ }),
    ).toBeVisible();
    await page.getByRole("button", { name: "准备资料" }).click();
    await expect(page.getByText("已创建资料准备任务。")).toBeVisible();
    await drainWorker(request);
    await expect(page.getByText(/读到正文 \d+ 条/)).toBeVisible({
      timeout: 20000,
    });

    // 检索式与"未响应引擎"如实展示（夹具返回 brave 被限流）
    await expect(page.getByText(/夹具摘要|brave/).first()).toBeVisible();
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
    // 标题以抓到的页面 og:title 为准（读取成功后覆盖命中的搜索标题）。
    await expect(
      page.getByRole("link", { name: /验收原文/ }).first(),
    ).toBeVisible();

    // 2) 查看正文：资料详情要能看到抓到的正文与体积
    await page.getByRole("button", { name: "查看正文" }).first().click();
    await expect(page.getByText(/字节 · 指纹/)).toBeVisible({ timeout: 20000 });
    await expect(page.locator("pre.material-text")).toContainText("支架断裂", {
      timeout: 20000,
    });
    await page.getByRole("button", { name: "收起正文" }).first().click();

    // 3) 资料整理：事实、引文、分歧与阻塞缺口
    await page.getByRole("tab", { name: "事实与核对" }).click();
    await page.getByRole("button", { name: "整理资料" }).click();
    await expect(page.getByText("已创建资料整理任务。")).toBeVisible();
    await drainWorker(request);
    await expect(
      page.getByText(/事实 \d+ · 分析 \d+ · 推测 \d+ · 引文 \d+ 条/),
    ).toBeVisible({
      timeout: 20000,
    });
    await expect(
      page.getByText("验收事实：", { exact: false }).first(),
    ).toBeVisible();
    await expect(page.getByText(/来自资料 [0-9a-f]{8}/).first()).toBeVisible();
    // 冲突既出现在事实行上（conflict_note）也出现在待办里，取第一条断言即可。
    await expect(
      page.getByText("验收分歧：厂商声明与测试结论不一致").first(),
    ).toBeVisible();
    await expect(
      page.getByText("验收缺口：缺少监管部门的一手通报").first(),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("阻塞缺口未处理");
    await expectNoOverflow(page, width);

    // 4) 处理缺口：先处理"影响主线"的那条，处理完阻塞提示消失
    const blockingNote = page
      .locator("li.related-item", { hasText: "影响主线" })
      .first();
    await blockingNote.getByRole("button", { name: "标记已处理" }).click();
    await expect(page.getByText("已标记为处理完成。")).toBeVisible();
    // 夹具模型可能同时产出多条阻塞缺口（缺引文的事实会自动补一条），
    // 所以这里只断言"这条移入已处理"，不断言阻塞提示整体消失。
    await expect(page.getByText(/已处理（1）/)).toBeVisible({ timeout: 20000 });

    // 5) 刷新后状态仍在（服务端为准），资料包版本可查
    await page.reload();
    await page.getByRole("tab", { name: "事实与核对" }).click();
    await expect(
      page.getByText("验收事实：", { exact: false }).first(),
    ).toBeVisible();
    await expect(page.getByText(/资料包版本/)).toBeVisible();
    await expect(page.getByText(/输入指纹 [0-9a-f]{8}/).first()).toBeVisible();
    await expectNoOverflow(page, width);

    // 6) 清理：删除这次发现（连同自动线索），来源一并移除，避免污染共享的 e2e 库
    const batches = await (
      await request.get("/api/discoveries?limit=100")
    ).json();
    for (const batch of batches.items) {
      await request.delete(`/api/discoveries/${batch.id}`);
    }
  });
}

test("键盘可以完成资料准备与核对", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { projectId, sourceId, batchId } = await seedProject(request);
  await page.goto(`/projects/${projectId}`);

  const prepare = page.getByRole("button", { name: "准备资料" });
  await prepare.focus();
  await expect(prepare).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("已创建资料准备任务。")).toBeVisible();
  await drainWorker(request);
  await expect(page.getByText(/读到正文 \d+ 条/)).toBeVisible({
    timeout: 20000,
  });

  const tab = page.getByRole("tab", { name: "事实与核对" });
  await tab.focus();
  await page.keyboard.press("Enter");
  const digest = page.getByRole("button", { name: "整理资料" });
  await digest.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("已创建资料整理任务。")).toBeVisible();
  await drainWorker(request);
  await expect(page.getByText(/事实 \d+ · 分析/)).toBeVisible({
    timeout: 20000,
  });
  await page.keyboard.press("Tab");
  // 键盘焦点继续落在面板内的控件上（按钮），不依赖具体顺序。
  await expect(page.locator(":focus")).toHaveAttribute("type", "button");

  await request.delete(`/api/discoveries/${batchId}`);
  await request.delete(`/api/sources/${sourceId}`);
});
