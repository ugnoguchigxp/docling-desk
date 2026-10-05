import {
  chromium,
  expect,
} from "../../frontend/node_modules/@playwright/test/index.mjs";
import { cp, readFile, writeFile } from "node:fs/promises";

// Use only the disposable browser-test server; never the application's data/.
const port = Number(process.env.WORD_QA_PORT || 18984);
if (!Number.isInteger(port) || port < 1024 || port > 65535)
  throw new Error("Invalid WORD_QA_PORT");
const id = "f".repeat(32);
const target = `.cache/frontend-migration-data-${port}/${id}`;
await cp(".cache/viewer-embedding/fixtures/docx", target, { recursive: true });
const job = JSON.parse(await readFile(`${target}/job.json`, "utf8"));
job.id = id;
await writeFile(`${target}/job.json`, JSON.stringify(job));
const browser = await chromium.launch({
  ...(process.env.UI_BROWSER_EXECUTABLE
    ? { executablePath: process.env.UI_BROWSER_EXECUTABLE }
    : { channel: "chrome" }),
  headless: true,
});
try {
  for (let run = 0; run < 3; run++) {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
    });
    await page.goto(`http://127.0.0.1:${port}/?job=${id}`);
    await expect(page.frameLocator("#original").locator("body")).toContainText(
      "Synthetic inventory evidence 120 items.",
    );
    await page.locator("#saveMenu summary").click();
    await page.locator("#printPreviewOpen").click();
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeEnabled();
    const print = page.frameLocator("#printPreviewFrame");
    await expect(print.locator(".print-body")).toContainText("120");
    await expect(print.locator("table")).toHaveCount(1);
    await page
      .locator("#printPreviewDialog")
      .getByRole("button", { name: "閉じる", exact: true })
      .click();
    await expect(page.locator("#printPreviewDialog")).toHaveCount(0);
    await expect(page.locator("#saveMenu summary")).toBeFocused();
    await page.close();
  }
  await writeFile(
    "qa/viewer-embedding/normal-word-results.json",
    JSON.stringify(
      {
        real_converted_docx: true,
        network_mock: false,
        mouse_print_open_and_close_runs: 3,
        text_and_table: true,
        focus_restored: true,
      },
      null,
      2,
    ) + "\n",
  );
} finally {
  await browser.close();
}
