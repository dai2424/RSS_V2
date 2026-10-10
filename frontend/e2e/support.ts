import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import type { APIRequestContext } from "@playwright/test";

/** 同步执行一次 worker；e2e 里用真实 worker 跑采集与加工任务。 */
export function runWorkerOnce() {
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
export async function drainWorker(request: APIRequestContext) {
  for (let i = 0; i < 20; i += 1) {
    runWorkerOnce();
    const page = await (await request.get("/api/tasks?status=queued")).json();
    if (!page.items.length) return;
  }
  throw new Error("任务队列未能排空，可能存在反复失败的任务。");
}

/** 取第一个可用行业分类；来源必须属于某个分类。 */
export async function firstCategoryId(request: APIRequestContext) {
  const categories = await (await request.get("/api/categories")).json();
  return categories[0].id as string;
}
