import { test, expect, type APIRequestContext } from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/** 造一个来源一条消息并跑完加工，用于删除验收。 */
async function seed(request: APIRequestContext, suffix: string) {
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "删除验收" + suffix,
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
  const sourceName = "删除验收来源" + suffix;
  const source = await (
    await request.post("/api/sources", {
      data: {
        name: sourceName,
        url: "http://127.0.0.1:8877/fixtures/feed.xml?case=delete" + suffix,
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
  const messages = await (await request.get("/api/messages?limit=50")).json();
  for (const message of messages.items) {
    await request.post(`/api/messages/${message.id}/enrich`);
  }
  await drainWorker(request);
  return sourceName;
}

/** 造一个有两版内容的消息：两次采集，夹具在第二次换掉第一条正文。 */
async function seedVersions(request: APIRequestContext, suffix: string) {
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "版本验收" + suffix,
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
  const sourceName = "版本验收来源" + suffix;
  const source = await (
    await request.post("/api/sources", {
      data: {
        name: sourceName,
        url: "http://127.0.0.1:8877/fixtures/two-items-feed.xml?case=" + suffix,
        platform: "验收",
        language: "auto",
        category_id: await firstCategoryId(request),
      },
    })
  ).json();
  for (let round = 0; round < 2; round += 1) {
    await request.post("/api/collection/runs", {
      data: { source_ids: [source.id] },
    });
    await drainWorker(request);
  }
  return sourceName;
}

for (const width of [1280, 1920]) {
  test(`消息删除与多选 ${width}px`, async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const suffix = String(Date.now());
    const sourceName = await seed(request, suffix);

    await page.goto("/messages");
    // 按来源收窄，断言只针对本次造的数据。
    await page.getByLabel("来源筛选").selectOption({ label: sourceName });
    // 行里显示的是加工产出的标题，因此按来源名定位这一行。
    const row = page.getByRole("row").filter({ hasText: sourceName });
    await expect(row.first()).toBeVisible({ timeout: 15000 });

    // 多选：勾选后出现批量条，清空选择后消失。
    await row.first().getByRole("checkbox").check();
    await expect(page.getByText("已选择 1 条消息")).toBeVisible();
    await page.getByRole("button", { name: "清空选择" }).click();
    await expect(page.getByText("已选择 1 条消息")).toBeHidden();

    // 行内删除：先看影响面与不可恢复提示，再确认。
    await row.first().getByRole("button", { name: "删除" }).click();
    const dialog = page.getByRole("alertdialog", { name: "删除消息" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("不可恢复");
    await expect(dialog).toContainText("下次采集时可能再次出现");
    await expect(dialog).toContainText("1 个版本");
    await page.screenshot({
      path: testInfo.outputPath(`messages-delete-${width}.png`),
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "确认删除" }).click();
    await expect(page.getByText(/已删除 1 条消息/)).toBeVisible();
    // 该来源下已经没有消息了。
    await expect(
      page.getByRole("row").filter({ hasText: "New computing" }),
    ).toHaveCount(0);
    await expect(page.getByText("没有匹配结果")).toBeVisible();

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });

  test(`详情页版本与任务状态 ${width}px`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const sourceName = await seedVersions(
      request,
      `detail-${width}-${Date.now()}`,
    );

    await page.goto("/messages");
    await page.getByLabel("来源筛选").selectOption({ label: sourceName });
    // 列表标题是机器产出的（译文或加工结果），按来源 + 版本号定位这一行。
    const row = page
      .getByRole("row")
      .filter({ hasText: sourceName })
      .filter({ hasText: "v2" });
    await expect(row.first()).toBeVisible({ timeout: 15000 });
    await row.first().getByRole("link").first().click();

    // 最新版本可以加工；切到历史版本只能看，按钮不再让人误以为加工的是眼前这一版。
    const enrichButton = page.getByRole("button", {
      name: "生成摘要",
      exact: true,
    });
    await expect(enrichButton).toBeVisible();
    await page.getByLabel("内容版本").selectOption({ index: 1 });
    await expect(
      page.getByText("历史版本只用于查看：翻译与内容加工在最新版本上进行。"),
    ).toBeVisible();
    await expect(enrichButton).toHaveCount(0);
    await page.getByLabel("内容版本").selectOption({ index: 0 });
    await expect(enrichButton).toBeVisible();

    // 任务状态只认服务端：排队时显示已排队，跑完后是不再禁用的已完成。
    await enrichButton.click();
    const status = page.getByRole("status").filter({ hasText: "加工任务" });
    await expect(status).toContainText("已排队");
    await page.screenshot({
      path: testInfo.outputPath(`message-detail-queued-${width}.png`),
      fullPage: true,
    });
    await drainWorker(request);
    await expect(status).toContainText("已完成", { timeout: 15000 });
    await expect(enrichButton).toBeEnabled({ timeout: 15000 });
    await page
      .getByRole("tab", { name: "内容摘要（机器生成）", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "精简标题", exact: true }),
    ).toBeVisible({ timeout: 15000 });

    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", width);
    expect(errors).toEqual([]);
  });
}
