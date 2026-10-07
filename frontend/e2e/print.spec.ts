import { expect, test, type Page } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { waitForViewerSurface } from "./viewer-ready";

const out = resolve(process.env.PRINT_QA_DIRECTORY || "../qa/print-preview");

for (const kind of ["SVG", "background", "lazy"])
  test(`${kind} images finish loading before printing becomes available`, async ({
    page,
  }) => {
    const id = "9".repeat(32);
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/files/${id}/delayed.png`, async (route) => {
      await pending;
      await route.fulfill({
        contentType: "image/png",
        body: await readFile(
          resolve(
            out,
            "fixtures/word/quicklook/original.docx.qlpreview/Attachment1.png",
          ),
        ),
      });
    });
    await page.route(`**/view/${id}/pages/1?**`, (route) =>
      route.fulfill({
        contentType: "text/html",
        body:
          kind === "SVG"
            ? `<svg xmlns="http://www.w3.org/2000/svg"><image href="/files/${id}/delayed.png" width="100" height="100"/></svg>`
            : kind === "background"
              ? `<div style="width:100px;height:100px;background-image:url(/files/${id}/delayed.png)"></div>`
              : `<img loading="lazy" style="margin-top:10000px" src="/files/${id}/delayed.png">`,
      }),
    );
    await openPrintDialog(page, id);
    await expect(
      page.frameLocator("#printPreviewFrame").locator(".print-unit"),
    ).toHaveCount(2);
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeDisabled();
    release();
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeEnabled();
  });

test("retry discovery failure stays blocked when paper settings change", async ({
  page,
}) => {
  const id = "9".repeat(32);
  let pageFails = true;
  let discoveryFails = false;
  await page.route(`**/view/${id}/pages/2?**`, (route) =>
    pageFails
      ? route.fulfill({ status: 500, body: "failure" })
      : route.continue(),
  );
  await page.route(`**/view/${id}/pdf`, (route) =>
    discoveryFails
      ? route.fulfill({ status: 503, body: "metadata failure" })
      : route.continue(),
  );
  await openPrintDialog(page, id);
  const dialog = page.locator("#printPreviewDialog");
  await expect(dialog.getByRole("alert")).toContainText("HTTP 500");
  pageFails = false;
  discoveryFails = true;
  await dialog.getByRole("button", { name: "再読み込み" }).click();
  await expect(dialog.getByRole("alert")).toContainText("HTTP 503");
  await page.getByLabel("印刷の用紙").selectOption("a4");
  await expect(dialog.getByRole("alert")).toContainText("HTTP 503");
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存" }),
  ).toBeDisabled();
  discoveryFails = false;
  await dialog.getByRole("button", { name: "再読み込み" }).click();
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存" }),
  ).toBeEnabled();
});

test("retry reloads a failed print shell", async ({ page }) => {
  let failed = true;
  await page.route("**/static/frontend/print.html", (route) =>
    failed ? route.abort() : route.continue(),
  );
  await openPrintDialog(page, "9".repeat(32));
  const dialog = page.locator("#printPreviewDialog");
  await expect(dialog.getByRole("alert")).toBeVisible();
  failed = false;
  await dialog.getByRole("button", { name: "再読み込み" }).click();
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存" }),
  ).toBeEnabled();
});

test("print removes sheet axes including their column width", async ({
  page,
}) => {
  await openPrint(page, "8".repeat(32));
  const frame = page.frameLocator("#printPreviewFrame");
  await expect(frame.locator(".sheet-column-axis,.sheet-row-axis")).toHaveCount(
    0,
  );
  const originalWidths = await page.request
    .get(`/view/${"8".repeat(32)}/sheets/1`)
    .then((r) => r.text());
  expect(originalWidths).toContain("width:44px");
  await expect(frame.locator("col[style='width:44px']")).toHaveCount(0);
  await expect(frame.locator("table").first()).toContainText("120");
});

test("every document format can print extracted content when original preview is unavailable", async ({
  page,
  request,
}) => {
  const id = "9".repeat(32);
  const job = await (await request.get(`/api/jobs/${id}`)).json();
  let filename = "without-preview.pdf";
  await page.route("**/api/library", (route) =>
    route.fulfill({
      json: {
        jobs: [
          {
            ...job,
            filename,
            original_filename: null,
            preview: null,
            slide_layout: false,
          },
        ],
        folders: [],
      },
    }),
  );
  for (const extension of ["pdf", "pptx", "xlsx", "docx", "md", "txt"]) {
    filename = `without-preview.${extension}`;
    await openPrint(page, id);
    await expect(page.getByRole("note")).toContainText(
      "抽出した本文・表・画像",
    );
    await expect(page.getByLabel("印刷の用紙")).toHaveValue("a4");
    await expect(page.getByLabel("印刷範囲", { exact: true })).toHaveCount(0);
    await expect(
      page.frameLocator("#printPreviewFrame").locator(".print-body"),
    ).toContainText("120");
    if (extension === "pdf") await savePrintedDocument(page, "without-preview");
    await page.getByRole("button", { name: "閉じる", exact: true }).click();
  }
});

test("available original preview can print even if text extraction failed", async ({
  page,
  request,
}) => {
  const id = "9".repeat(32);
  const job = await (await request.get(`/api/jobs/${id}`)).json();
  await page.route("**/api/library", (route) =>
    route.fulfill({
      json: {
        jobs: [{ ...job, state: "failed", error: "抽出に失敗しました" }],
        folders: [],
      },
    }),
  );
  await openPrint(page, id);
  await expect(
    page.frameLocator("#printPreviewFrame").locator(".print-unit"),
  ).toHaveCount(2);
});
async function openPrintDialog(page: Page, id: string, previewText?: string) {
  await page.goto(`/?job=${id}`);
  await waitForViewerSurface(page);
  const source = page.locator("#original:visible,#slideCanvas iframe:visible");
  if (previewText)
    await expect(page.frameLocator("#original").locator("body")).toContainText(
      previewText,
    );
  await page.locator("#saveMenu summary").click();
  await expect(page.locator("#saveMenu")).toHaveAttribute("open", "");
  if (await source.count())
    await expect(source).toHaveCSS("pointer-events", "none");
  const trigger = page.getByRole("button", {
    name: "プリントプレビュー",
    exact: true,
  });
  if (previewText) {
    // The Word fixture additionally covers accessible keyboard activation.
    await trigger.focus();
    await trigger.press("Enter");
  } else await trigger.click();
  await expect(page.locator("#printPreviewDialog")).toBeVisible();
}
async function openPrint(page: Page, id: string, previewText?: string) {
  await openPrintDialog(page, id, previewText);
  await expect(page.getByRole("button", { name: "印刷・PDF保存" })).toBeEnabled(
    { timeout: 45000 },
  );
}
async function savePrintedDocument(page: Page, name: string) {
  // Mirror the exact printable DOM and shadow trees into a temporary top-level
  // document so Chromium's automated PDF export can exercise pagination.
  const snapshot = await page.locator("#printPreviewFrame").evaluate((node) => {
    const doc = (node as HTMLIFrameElement).contentDocument!;
    return {
      head: doc.head.innerHTML,
      body: doc.body.innerHTML,
      shadows: [...doc.querySelectorAll(".print-unit>div")].map(
        (n) => n.shadowRoot!.innerHTML,
      ),
    };
  });
  const printed = await page.context().newPage();
  await printed.goto(new URL("/static/frontend/print.html", page.url()).href);
  await printed.evaluate((data) => {
    document.open();
    document.write(
      `<!doctype html><html><head>${data.head}</head><body>${data.body}</body></html>`,
    );
    document.close();
    document
      .querySelectorAll(".print-unit>div")
      .forEach(
        (n, i) =>
          (n.attachShadow({ mode: "open" }).innerHTML = data.shadows[i]),
      );
  }, snapshot);
  await printed.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      [...document.querySelectorAll(".print-unit>div")]
        .flatMap((n) => [...n.shadowRoot!.querySelectorAll("img")])
        .map((img) => img.decode().catch(() => {})),
    );
  });
  await printed.pdf({
    path: `${out}/${name}.pdf`,
    preferCSSPageSize: true,
    printBackground: true,
  });
  await printed.close();
}

for (const [kind, id] of [
  ["pdf", "9".repeat(32)],
  ["slides", "7".repeat(32)],
  ["sheets", "8".repeat(32)],
  ["synthetic-glyphs", "a".repeat(32)],
  ["synthetic-workbook", "b".repeat(32)],
]) {
  test(`print ${kind}: whole document, current unit, custom range and restore viewer`, async ({
    page,
  }) => {
    await mkdir(out, { recursive: true });
    await openPrint(page, id);
    const frame = page.frameLocator("#printPreviewFrame");
    const count = await frame.locator(".print-unit").count();
    expect(count).toBeGreaterThan(1);
    await savePrintedDocument(page, kind);
    await page.screenshot({ path: `${out}/${kind}.png` });
    await page.getByLabel("印刷範囲", { exact: true }).selectOption("current");
    await expect(frame.locator(".print-unit")).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeEnabled();
    await page.getByLabel("印刷範囲", { exact: true }).selectOption("range");
    await page.getByLabel("印刷する範囲").fill("0");
    await expect(page.getByRole("alert")).toContainText("範囲は1");
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeDisabled();
    await page.getByLabel("印刷する範囲").fill("2");
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeEnabled();
    await expect(frame.locator(".print-unit")).toHaveAttribute(
      "data-number",
      "2",
    );
    await page.getByLabel("印刷の用紙").selectOption("a4");
    await page.getByLabel("印刷の向き").selectOption("landscape");
    await expect(
      page.getByRole("button", { name: "印刷・PDF保存" }),
    ).toBeEnabled();
    await savePrintedDocument(page, `${kind}-a4`);
    await page.locator("#printPreviewFrame").evaluate((node) => {
      const w = (node as HTMLIFrameElement).contentWindow!;
      w.print = () => {
        (node as HTMLElement).dataset.printCalled = "true";
      };
    });
    await page.getByRole("button", { name: "印刷・PDF保存" }).click();
    await expect(page.locator("#printPreviewFrame")).toHaveAttribute(
      "data-print-called",
      "true",
    );
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("dialog", { name: "プリントプレビュー" }),
    ).toHaveCount(0);
    await expect(page.locator("#filename")).not.toBeEmpty();
  });
}

test("long text prints across pages, retaining the final line and excluding application controls", async ({
  page,
}) => {
  await mkdir(out, { recursive: true });
  const content =
    Array.from(
      { length: 180 },
      (_, i) => `印刷の確認 ${i + 1}: 日本語の本文と数字 120`,
    ).join("\n") + "\nPRINT_FINAL_LINE";
  await page.goto("/?mode=library");
  await page.locator("#addFile").click();
  await page.locator("#upload input[type=file]").setInputFiles({
    name: "print-check.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(content),
  });
  await page.locator("#upload button[type=submit]").click();
  await expect(page.locator("#uploadDialog")).toBeHidden();
  await page.getByLabel("print-check.txtを開く", { exact: true }).click();
  await expect(page.locator("#filename")).toHaveText("print-check.txt");
  const id = new URL(page.url()).searchParams.get("job")!;
  await openPrint(page, id);
  await expect(
    page.frameLocator("#printPreviewFrame").locator("pre"),
  ).toContainText("PRINT_FINAL_LINE");
  await savePrintedDocument(page, "long-text");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存" }),
  ).toBeVisible();
  await page.screenshot({ path: `${out}/mobile.png` });
});

test("failed page loading blocks printing and supports retry", async ({
  page,
}) => {
  const id = "9".repeat(32);
  let fail = true;
  await page.route(`**/view/${id}/pages/2?**`, (route) =>
    fail ? route.fulfill({ status: 500, body: "failure" }) : route.continue(),
  );
  await openPrintDialog(page, id);
  await expect(
    page.locator("#printPreviewDialog").getByRole("alert"),
  ).toContainText("HTTP 500");
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存" }),
  ).toBeDisabled();
  fail = false;
  await page
    .locator("#printPreviewDialog")
    .getByRole("button", { name: "再読み込み" })
    .click();
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存" }),
  ).toBeEnabled();
});

test("Word uses the saved Quick Look document with text, table and image", async ({
  page,
}) => {
  const fixture = resolve(out, "fixtures/word");
  const job = JSON.parse(await readFile(`${fixture}/job.json`, "utf8"));
  await page.route("**/api/library", (route) =>
    route.fulfill({ json: { jobs: [job], folders: [] } }),
  );
  await page.route(`**/view/${job.id}/word**`, async (route) =>
    route.fulfill({
      contentType: "text/html",
      body: await readFile(`${fixture}/preview.html`, "utf8"),
    }),
  );
  await page.route(`**/files/${job.id}/quicklook/**`, async (route) => {
    const relative = new URL(route.request().url()).pathname.split(
      `/files/${job.id}/`,
    )[1];
    const path = resolve(fixture, relative);
    if (!path.startsWith(`${fixture}/`)) return route.abort();
    await route.fulfill({
      contentType: path.endsWith(".png") ? "image/png" : "text/css",
      body: await readFile(path),
    });
  });
  await openPrint(page, job.id, "Wordの表示とコピーを確認する");
  const frame = page.frameLocator("#printPreviewFrame");
  await expect(frame.locator(".print-body")).toContainText(
    "Wordの表示とコピーを確認する",
  );
  await expect(frame.locator("table")).toContainText("120");
  await expect(frame.locator("img")).toBeVisible();
  await savePrintedDocument(page, "word");
  await page.screenshot({ path: `${out}/word.png` });
});

test("Markdown prints the formatted heading and table", async ({ page }) => {
  await page.goto("/?mode=library");
  await page.locator("#addFile").click();
  await page.locator("#upload input[type=file]").setInputFiles({
    name: "print-markdown.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# 印刷の見出し\n\n本文と **強調**\n\n| 区分 | 件数 |\n| --- | --- |\n| A | 120 |\n",
    ),
  });
  await page.locator("#upload button[type=submit]").click();
  await expect(page.locator("#uploadDialog")).toBeHidden();
  await page.getByLabel("print-markdown.mdを開く", { exact: true }).click();
  const id = new URL(page.url()).searchParams.get("job")!;
  await openPrint(page, id);
  await expect(
    page
      .frameLocator("#printPreviewFrame")
      .getByRole("heading", { name: "印刷の見出し" }),
  ).toBeVisible();
  await savePrintedDocument(page, "markdown");
});
