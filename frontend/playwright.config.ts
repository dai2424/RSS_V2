import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: "http://127.0.0.1:8877",
    channel: process.env.PLAYWRIGHT_CHANNEL || "msedge",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "uv run --project .. python ../tests/preview.py",
    url: "http://127.0.0.1:8877/api/health",
    timeout: 60000,
    reuseExistingServer: false,
  },
  reporter: "list",
});
