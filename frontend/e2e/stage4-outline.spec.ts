import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { drainWorker, firstCategoryId } from "./support";

/**
 * 阶段 4 提纲（P4）验收：生成 → 查看 → 修改另存 → 确认，以及未保存离开保护。
 *
 * 模型来自夹具端点：提纲里的分节引用提示词里出现的事实 id，服务端核对这些 id 属于
 * 本次资料包，因此界面看到的是真实链路的结果。夹具的阻塞缺口不拦确认——按已定方案
 * 只做显著标记。
 */

function uniqueSuffix() {
  return `outline-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

/** 走一遍"发现选题 → 选择候选 → 资料准备 → 资料整理"，返回项目 id。 */
async function seedOutlinedProject(request: APIRequestContext) {
  const suffix = uniqueSuffix();
  const provider = await (
    await request.post("/api/llm/providers", {
      data: {
        name: "提纲验收" + suffix,
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
        name: "提纲验收来源" + suffix,
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

  // 提纲需要确定的资料包：把资料准备与整理都在这里跑完。
  await request.post(`/api/projects/${project.id}/materials`);
  await drainWorker(request);
  await request.post(`/api/projects/${project.id}/facts`);
  await drainWorker(request);
  return { projectId: project.id as string, batchId: created.id as string };
}

/** 页面无横向溢出：body 的 scrollWidth 等于视口宽度。 */
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
  test(`提纲生成到确认（${width}px）`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    const { projectId } = await seedOutlinedProject(request);

    await page.goto(`/projects/${projectId}`);
    await page.getByRole("tab", { name: "提纲" }).click();
    await expect(page.getByText("还没有提纲")).toBeVisible();

    // 1) 生成：创建后台任务，worker 执行真实调用
    await page.getByRole("button", { name: "生成提纲" }).click();
    await expect(page.getByText("已创建提纲生成任务。")).toBeVisible();
    await drainWorker(request);
    await expect(page.getByText("验收：夹具提纲").first()).toBeVisible({
      timeout: 20000,
    });
    await expect(page.getByText("第 1 版").first()).toBeVisible();
    await expect(page.getByText("发生了什么").first()).toBeVisible();
    await expect(page.getByText("行业影响").first()).toBeVisible();
    // 阻塞缺口只标记、不拦确认（是否允许生成全文留给 P5）。
    await expect(page.getByText(/阻塞缺口未处理/)).toBeVisible();
    await expectNoOverflow(page, width);

    // 2) 确认：项目状态推进，界面给出确认版本
    await page.getByRole("button", { name: "确认这一版" }).first().click();
    await expect(page.getByText("已确认第 1 版提纲。")).toBeVisible();
    await expect(page.getByText("当前确认版本")).toBeVisible({
      timeout: 20000,
    });
    await expect(page.getByText(/状态：提纲已确认/)).toBeVisible();

    // 3) 修改另存：新草稿版本，旧确认版本保留，并提示"已有更新版本尚未确认"
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.getByLabel("文章标题").fill("人工改过的标题");
    await page.getByRole("button", { name: "保存为新版本" }).click();
    await expect(page.getByText("已保存为第 2 版草稿。")).toBeVisible();
    await expect(page.getByText("第 2 版").first()).toBeVisible({
      timeout: 20000,
    });
    await expect(page.getByText(/已确认的版本不是最新版本/)).toBeVisible();
    await expect(page.getByText("人工改过的标题").first()).toBeVisible();
    await expectNoOverflow(page, width);

    // 4) 刷新后仍以服务端为准
    await page.reload();
    await page.getByRole("tab", { name: "提纲" }).click();
    await expect(page.getByText("人工改过的标题").first()).toBeVisible();
    await expect(
      page.getByText("已确认", { exact: true }).first(),
    ).toBeVisible();
    await expectNoOverflow(page, width);

    await clearDiscoveries(request);
  });
}

test("键盘可以完成生成与确认，未保存离开有保护", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { projectId } = await seedOutlinedProject(request);
  await page.goto(`/projects/${projectId}`);

  const tab = page.getByRole("tab", { name: "提纲" });
  await tab.focus();
  await page.keyboard.press("Enter");

  const generate = page.getByRole("button", { name: "生成提纲" });
  await generate.focus();
  await expect(generate).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("已创建提纲生成任务。")).toBeVisible();
  await drainWorker(request);
  await expect(page.getByText("验收：夹具提纲").first()).toBeVisible({
    timeout: 20000,
  });

  // 未保存离开保护：编辑器里有改动时切分区先确认，继续编辑不丢内容。
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const title = page.getByLabel("文章标题");
  await title.fill("键盘改过的标题");
  await page.getByRole("tab", { name: "资料" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("未保存的修改");
  await page.getByRole("button", { name: "继续编辑" }).click();
  await expect(title).toHaveValue("键盘改过的标题");

  // 放弃修改后离开：编辑器卸载，回到资料分区。
  await page.getByRole("tab", { name: "资料" }).click();
  await page.getByRole("button", { name: "放弃修改并离开" }).click();
  await expect(page.getByRole("tab", { name: "资料" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByLabel("文章标题")).toHaveCount(0);

  // 回到提纲分区：刚才的改动没有落库，仍是第 1 版。
  await page.getByRole("tab", { name: "提纲" }).click();
  await expect(page.getByText("键盘改过的标题")).toHaveCount(0);

  await clearDiscoveries(request);
});
