import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/**
 * 阶段 4 全文（P5）验收：确认提纲 → 生成全文 → 阅读 → 点引用标记核对证据。
 *
 * 模型来自夹具端点：正文里的引用标记引用提示词里出现的事实 id，服务端核对归属后
 * 落成版本；预览由服务端渲染受控 HTML，点标记就地展开证据（不再发请求）。
 */

function uniqueSuffix() {
  return `article-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

/** 走一遍"发现选题 → 资料准备 → 资料整理"，返回项目 id。 */
async function seedProject(request: APIRequestContext) {
  const suffix = uniqueSuffix();
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "全文验收" + suffix,
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
        name: "全文验收来源" + suffix,
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

  await request.post(`/api/projects/${project.id}/materials`);
  await drainWorker(request);
  await request.post(`/api/projects/${project.id}/facts`);
  await drainWorker(request);
  return { projectId: project.id as string, batchId: created.id as string };
}

/** 生成并确认提纲：全文生成的前置。 */
async function confirmOutline(
  page: Page,
  request: APIRequestContext,
  projectId: string,
) {
  await page.goto(`/projects/${projectId}`);
  await page.getByRole("tab", { name: "提纲" }).click();
  await page.getByRole("button", { name: "生成提纲" }).click();
  await drainWorker(request);
  await expect(page.getByText("验收：夹具提纲").first()).toBeVisible({
    timeout: 20000,
  });
  await page.getByRole("button", { name: "确认这一版" }).first().click();
  await expect(page.getByText(/已确认第 1 版提纲/)).toBeVisible();
  await expect(page.getByText("当前确认版本")).toBeVisible({ timeout: 20000 });
}

async function expectNoOverflow(page: Page, width: number) {
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
}

async function clearDiscoveries(request: APIRequestContext) {
  const batches = await (
    await request.get("/api/discoveries?limit=100")
  ).json();
  for (const batch of batches.items) {
    await request.delete(`/api/discoveries/${batch.id}`);
  }
}

for (const width of [1280, 1920]) {
  test(`全文生成到引用核对（${width}px）`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    const { projectId } = await seedProject(request);
    await confirmOutline(page, request, projectId);

    // 1) 生成全文
    await page.getByRole("tab", { name: "文章" }).click();
    await expect(page.getByText("还没有全文")).toBeVisible();
    await page.getByRole("button", { name: "生成全文" }).click();
    await expect(page.getByText("已创建全文生成任务。")).toBeVisible();
    await drainWorker(request);
    await expect(page.getByText("验收：夹具全文").first()).toBeVisible({
      timeout: 20000,
    });
    // 阻塞缺口只标记、不拦生成。
    await expect(page.getByText(/阻塞缺口未处理/)).toBeVisible();
    await expectNoOverflow(page, width);

    // 2) 阅读：正文小标题、文末来源列表、单一来源提示
    await expect(
      page.getByRole("heading", { name: "发生了什么" }),
    ).toBeVisible();
    await expect(page.locator(".source-list li").first()).toBeVisible();
    await expect(page.getByText(/本篇只有一个来源/)).toBeVisible();

    // 3) 点引用标记就地展开证据（不离开阅读位置）
    const marker = page.locator("button.citation-marker").first();
    await expect(marker).toBeVisible();
    await marker.click();
    await expect(
      page.getByRole("heading", { name: /引用 \[\^1\]/ }),
    ).toBeVisible();
    const sheet = page.getByRole("dialog");
    await expect(page.getByText("引文原文", { exact: true })).toBeVisible();
    await expect(sheet.locator(".citation-quote")).toBeVisible();
    await expect(sheet.getByRole("link").first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("heading", { name: /引用 \[\^1\]/ }),
    ).toBeHidden();

    // 4) 刷新后仍以服务端为准
    await page.reload();
    await page.getByRole("tab", { name: "文章" }).click();
    await expect(page.getByText("验收：夹具全文").first()).toBeVisible();
    await expectNoOverflow(page, width);

    await clearDiscoveries(request);
  });
}

test("键盘可以生成全文并打开引用证据", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { projectId } = await seedProject(request);
  await confirmOutline(page, request, projectId);

  const tab = page.getByRole("tab", { name: "文章" });
  await tab.focus();
  await page.keyboard.press("Enter");

  const generate = page.getByRole("button", { name: "生成全文" });
  await generate.focus();
  await expect(generate).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("已创建全文生成任务。")).toBeVisible();
  await drainWorker(request);
  await expect(page.getByText("验收：夹具全文").first()).toBeVisible({
    timeout: 20000,
  });

  // 引用标记是真正的按钮：可以用键盘聚焦并展开证据。
  const marker = page.locator("button.citation-marker").first();
  await marker.focus();
  await expect(marker).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: /引用 \[\^1\]/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("heading", { name: /引用 \[\^1\]/ }),
  ).toBeHidden();

  await clearDiscoveries(request);
});
