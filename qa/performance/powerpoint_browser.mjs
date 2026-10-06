// Runs against an isolated local QA server; no provider or deployment calls.
import {
  chromium,
  expect as baseExpect,
} from "../../frontend/node_modules/@playwright/test/index.mjs";
import { mkdir, writeFile } from "node:fs/promises";
const expect = baseExpect.configure({ timeout: 30000 });
const base = process.env.DOCLING_QA_URL || "http://127.0.0.1:8895";
const output = new URL("./powerpoint-browser/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
});
await context.addInitScript(() => {
  if (window !== window.top) return;
  Object.defineProperty(navigator, "clipboard", {
    value: {
      writeText: async (text) => {
        document.documentElement.dataset.copiedText = text;
      },
    },
  });
});
const page = await context.newPage();
const issues = [];
page.on("pageerror", (e) => issues.push(e.message));
const id = "a".repeat(32);
await page.goto(`${base}/?job=${id}`);
await expect(page.locator("#slidePicker option")).toHaveCount(530);
const started = performance.now();
for (let n = 1; n <= 530; n++) {
  await page.locator("#slidePicker").selectOption(String(n));
  const frame = page.frameLocator("#slideCanvas iframe");
  await expect(frame.locator("svg")).toHaveAttribute(
    "data-text-layout",
    new RegExp(`"number": ${n},`),
  );
  await expect(frame.locator("svg")).toContainText(
    `Slide ${n}: office file flow affinity`,
  );
  // Layout and font shaping must complete, not just an HTTP success.
  const handle = await page.locator("#slideCanvas iframe").elementHandle();
  const inner = await handle.contentFrame();
  await inner.evaluate(() => document.fonts.ready);
  const textBox = await frame.locator("text").first().boundingBox();
  if (!textBox || textBox.width <= 0)
    throw new Error(`Page ${n} has no visible glyphs`);
  if (n % 100 === 0) console.log(`Rendered ${n}/530 pages`);
}
await page.screenshot({ path: new URL("slide-530.png", output).pathname });
const allPagesSeconds = (performance.now() - started) / 1000;
await page.goto(`${base}/?job=${"c".repeat(32)}`);
await page.locator("#slidePicker").selectOption("2");
const frameHandle = await page.locator("#slideCanvas iframe").elementHandle();
const inner = await frameHandle.contentFrame();
await inner.waitForSelector("[data-glyph-clusters]");
const selected = await inner.evaluate(async () => {
  await document.fonts.ready;
  const node = document.querySelector("[data-glyph-clusters]");
  const range = document.createRange();
  range.selectNodeContents(node);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
  return {
    text: selection.toString(),
    widths: [...node.children].map((s) => ({
      text: s.textContent,
      width: s.getComputedTextLength(),
    })),
  };
});
expect(selected.text).toBe("office fi fl ff ffi ffl finish");
await expect(page.locator("#previewCopy")).toBeEnabled();
await page.locator("#previewCopy").click();
await expect(page.locator("html")).toHaveAttribute(
  "data-copied-text",
  selected.text,
);
await page.screenshot({ path: new URL("ligatures-copy.png", output).pathname });
// Simulate a persistent status failure and observe actual network requests.
let failedReads = 0;
await page.route("**/api/jobs/*/translations", async (route) => {
  failedReads++;
  await route.fulfill({
    status: 422,
    contentType: "application/json",
    body: JSON.stringify({ detail: "原文プレビューを読み込めません。" }),
  });
});
await page.goto(`${base}/?job=${id}`);
await expect(page.locator("#translationStatus")).toContainText(
  "原文プレビューを読み込めません。",
);
await page.waitForTimeout(6500);
expect(failedReads).toBe(1);
expect(issues).toEqual([]);
const report = {
  renderedPages: 530,
  allPagesSeconds,
  selectedText: selected.text,
  clusterWidths: selected.widths,
  statusErrorRequestsIn6_5Seconds: failedReads,
  issues,
};
await writeFile(
  new URL("report.json", output),
  JSON.stringify(report, null, 2),
);
console.log(JSON.stringify(report));
await browser.close();
