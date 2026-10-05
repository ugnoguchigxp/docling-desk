import { defineConfig } from "@playwright/test";
const dev = !!process.env.UI_DEV;
const port = Number(process.env.UI_QA_PORT || (dev ? 8877 : 8876));
const browserExecutable = process.env.UI_BROWSER_EXECUTABLE;
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile: `${process.env.UI_REPORT_DIRECTORY || "../qa/frontend-migration"}/${dev ? "dev-browser-results" : process.argv.some((arg) => arg.includes("layout")) ? "layout-browser-results" : "browser-results"}.json`,
      },
    ],
  ],
  use: {
    baseURL: dev ? "http://127.0.0.1:5187" : `http://127.0.0.1:${port}`,
    browserName: "chromium",
    channel: browserExecutable || process.env.CI ? undefined : "chrome",
    headless: true,
    launchOptions: process.argv.some((arg) => arg.includes("layout"))
      ? {
          executablePath: browserExecutable,
          args: ["--disable-gpu", "--force-color-profile=srgb"],
        }
      : { executablePath: browserExecutable },
    viewport: { width: 1440, height: 900 },
  },
  webServer: [
    {
      command: `DOCLING_UI_QA_PORT=${port} ${process.env.DOCLING_PYTHON || "../.venv/bin/python"} ../qa/frontend-migration/server.py`,
      url: `http://127.0.0.1:${port}/api/library`,
      reuseExistingServer: false,
      timeout: 60000,
    },
    ...(dev
      ? [
          {
            command: `UI_BACKEND=http://127.0.0.1:${port} pnpm dev --port 5187`,
            url: "http://127.0.0.1:5187",
            reuseExistingServer: false,
            timeout: 60000,
          },
        ]
      : []),
  ],
  projects: [
    { name: "wiki", testMatch: "wiki.spec.ts" },
    { name: "printing", testMatch: "print.spec.ts" },
    { name: "operations", testMatch: "operations.spec.ts" },
    { name: "layout", testMatch: "layout.spec.ts", timeout: 1800000 },
    { name: "saved", testMatch: "saved.spec.ts" },
  ],
});
