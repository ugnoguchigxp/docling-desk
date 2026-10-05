import {
  chromium,
  expect,
} from "../../frontend/node_modules/@playwright/test/index.mjs";
import { readFile, writeFile } from "node:fs/promises";
const config = JSON.parse(
  await readFile(".cache/viewer-embedding/config.json", "utf8"),
);
const refs = JSON.parse(
  await readFile(".cache/viewer-embedding/refs.json", "utf8"),
);
const [alice, bob] = Object.keys(config.users);
const tool = async (name, user, args) => {
  const r = await fetch(config.public_url + "/host/tools/" + name, {
    method: "POST",
    headers: {
      authorization: "Bearer " + config.connector_token,
      "content-type": "application/json",
    },
    body: JSON.stringify({ user_id: user, arguments: args }),
  });
  return { status: r.status, body: await r.json() };
};
const browser = await chromium.launch({
  ...(process.env.UI_BROWSER_EXECUTABLE
    ? { executablePath: process.env.UI_BROWSER_EXECUTABLE }
    : { channel: "chrome" }),
  headless: true,
});
const results = { browser: browser.version(), formats: {} };
try {
  for (const [suffix, ref] of Object.entries(refs)) {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
    });
    const page = await context.newPage();
    const errors = [];
    const requests = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("response", (r) => {
      requests.push({
        path: new URL(r.url()).pathname.replace(
          /\/session\/[^/]+/,
          "/session/[redacted]",
        ),
        status: r.status(),
      });
    });
    const kind = {
      pdf: "page",
      pptx: "slide",
      xlsx: "sheet",
      docx: "document",
      md: "document",
      txt: "document",
    }[suffix];
    const result = await tool("request_document_view", alice, {
      ...ref,
      location: { kind, number: kind === "document" ? 1 : 2 },
    });
    expect(result.status).toBe(200);
    await page.setContent(
      '<iframe id="embed" sandbox="allow-scripts allow-downloads" style="width:100%;height:780px"></iframe>',
    );
    await page
      .locator("#embed")
      .evaluate((el, url) => (el.src = url), result.body.embed_url);
    const frame = page.frameLocator("#embed");
    const code = frame.getByLabel("接続コード");
    await expect(code).toBeVisible();
    const approval = await tool("connect_document_view", alice, {
      code: await code.textContent(),
    });
    expect(approval.status).toBe(200);
    await expect(frame.locator("#detail")).toBeVisible({ timeout: 20000 });
    if (suffix === "pdf")
      await expect(
        frame.frameLocator("#original").locator("#pdfPage"),
      ).toHaveValue("2");
    if (suffix === "pptx")
      await expect(frame.locator("#slidePicker")).toHaveValue("2");
    if (suffix === "xlsx")
      await expect(
        frame
          .frameLocator("#original")
          .locator("#sheetTabs [aria-selected=true]"),
      ).toContainText("注記");
    if (kind === "document")
      await expect(
        frame.frameLocator("#original").locator("body"),
      ).toContainText("Synthetic inventory evidence");
    await frame.locator("#tab-tables").click();
    await expect(frame.locator("#tables")).toBeVisible();
    await frame.locator("#tab-structure").click();
    await expect(frame.locator("#structure")).toBeVisible();
    await frame.locator("#tab-rag").click();
    await expect(frame.locator("#rag")).toBeVisible();
    await frame.locator("#tab-preview").click();
    await expect(
      frame.locator(suffix === "pptx" ? "#slideCanvas iframe" : "#original"),
    ).toBeVisible();
    if (suffix === "pdf") {
      const pdf = frame.frameLocator("#original");
      await expect(pdf.locator("#pdfPage")).toHaveValue("2");
      await expect(
        pdf
          .frameLocator('.pdf-page[data-number="2"] iframe')
          .getByText("図と表の参照を確認する", { exact: true }),
      ).toBeVisible();
      await pdf.locator("#pdfZoom").selectOption("150");
      await pdf.locator("#pdfFit").click();
    } else if (suffix === "pptx") {
      await expect(frame.locator("#slidePicker")).toHaveValue("2");
      await frame.locator("#slideZoomPreset").selectOption("1.5");
      await frame.locator("#slideFit").click();
    } else if (suffix === "xlsx") {
      const workbook = frame.frameLocator("#original");
      await workbook.locator("#sheetZoomIn").click();
      await workbook.locator("#sheetFit").click();
    } else {
      await expect(
        frame
          .frameLocator("#original")
          .getByText("Synthetic inventory evidence 120 items.", {
            exact: false,
          }),
      ).toBeVisible();
      await frame
        .getByRole("combobox", { name: /ズーム倍率/ })
        .selectOption("1.5");
      await frame.getByRole("button", { name: "幅に合わせる" }).click();
    }
    // Capture after the retained frame has been painted following tab restoration.
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    await page.screenshot({
      path: `qa/viewer-embedding/${suffix}-sandbox.png`,
    });
    for (const width of [800, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(frame.locator("#detail")).toBeVisible();
    }
    await page.reload(); // Parent is empty; recreate persisted stable reference in a fresh frame.
    await page.setContent(
      '<iframe id="embed" sandbox="allow-scripts allow-downloads" style="width:100%;height:740px"></iframe>',
    );
    await page
      .locator("#embed")
      .evaluate((el, url) => (el.src = url), result.body.embed_url);
    await expect(
      page.frameLocator("#embed").getByLabel("接続コード"),
    ).toBeVisible();
    expect(
      (
        await tool("connect_document_view", bob, {
          code: await page
            .frameLocator("#embed")
            .getByLabel("接続コード")
            .textContent(),
        })
      ).status,
    ).toBe(404);
    expect(
      requests.filter(
        (r) =>
          r.path.includes("/api/library") ||
          (r.path.startsWith("/api/") &&
            (r.path.includes("translations") ||
              r.path.includes("explanation"))),
      ),
    ).toEqual([]);
    expect(errors).toEqual([]);
    const failed = requests.filter((r) => r.status >= 400);
    expect(failed).toEqual([]);
    results.formats[suffix] = {
      initial_unit: true,
      tabs: true,
      zoom_and_fit: true,
      restored_document_visible: true,
      widths: [1280, 800, 390],
      narrow: true,
      reopen_requires_pairing: true,
      other_user_denied: true,
      library_or_generation_requests: 0,
      failed_resources: failed,
      errors,
    };
    await context.close();
  }
  await writeFile(
    "qa/viewer-embedding/formats-results.json",
    JSON.stringify(results, null, 2) + "\n",
  );
  console.log("All six opaque-sandbox format checks passed");
} finally {
  await browser.close();
}
