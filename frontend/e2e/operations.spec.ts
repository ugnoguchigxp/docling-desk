import { test, expect, type Page } from "@playwright/test";
import { waitForViewerSurface } from "./viewer-ready";
const diagnostics = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => {
  const issues: string[] = [];
  diagnostics.set(page, issues);
  page.on("pageerror", (error) => issues.push(error.message));
  page.on("console", (message) => {
    if (
      ["error", "warning"].includes(message.type()) &&
      /React|Each child|uncontrolled|controlled input|Cannot update a component/.test(
        message.text(),
      )
    )
      issues.push(message.text());
  });
});
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  expect(diagnostics.get(page)).toEqual([]);
});
const slide = "7".repeat(32),
  sheet = "8".repeat(32),
  pdf = "9".repeat(32);

for (const extension of ["md", "markdown", "txt", "text"]) {
  test(`text document upload and preview: ${extension}`, async ({ page }) => {
    const markdown = ["md", "markdown"].includes(extension);
    const content = markdown
      ? "# 日本語の見出し\n\n本文と **強調**。\n\n| 区分 | 件数 |\n| --- | --- |\n| A | 120 |\n"
      : "# 記号をそのまま表示\n  字下げと *記号* & <tag>\n\n次の本文。\n";
    const name = `browser-sample.${extension}`;
    await page.goto("/?mode=library");
    await page.locator("#addFile").click();
    await page.locator("#upload input[type=file]").setInputFiles({
      name,
      mimeType: markdown ? "text/markdown" : "text/plain",
      buffer: Buffer.from(content),
    });
    await page.locator("#upload button[type=submit]").click();
    await expect(page.locator("#uploadDialog")).toBeHidden();
    await page.locator("#fileType").selectOption(markdown ? "md" : "txt");
    const row = page.locator("#library tr").filter({ hasText: name });
    await expect(row).toContainText("抽出完了");
    await expect(row).toContainText("文書全体");
    await page.getByLabel(`${name}を開く`, { exact: true }).click();
    await expect(page.locator("#tab-preview")).toHaveText("本文プレビュー");
    const preview = page.frameLocator("#original");
    if (markdown) {
      await expect(
        preview.getByRole("heading", { name: "日本語の見出し" }),
      ).toBeVisible();
      await expect(preview.locator("strong")).toHaveText("強調");
      await expect(preview.locator("table")).toContainText("120");
    } else {
      await expect(preview.locator("pre.plain-text")).toHaveText(content);
      await expect(preview.locator("pre.plain-text")).toHaveCSS(
        "white-space",
        "pre-wrap",
      );
    }
    await expect(page.getByLabel("文書のズーム倍率")).toBeEnabled();
    await page.locator("#tab-structure").click();
    await expect(page.locator("#structure")).toContainText(
      markdown ? "日本語の見出し" : "記号をそのまま表示",
    );
    await page.locator("#tab-rag").click();
    await expect(page.locator("#rag")).toContainText(
      markdown ? "120" : "次の本文",
    );
    await page.locator("#tab-preview").click();
    await expect(page.locator("#explainOpen")).toBeEnabled();
    await page.locator("#saveMenu summary").click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.locator("#originalDownload").click(),
    ]);
    expect(download.suggestedFilename()).toBe(name);
  });
}
async function capturePreviewClipboard(page: Page) {
  // Exercise the toolbar without changing the operating system clipboard.
  await page.addInitScript(() => {
    if (window !== window.top) return;
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async (text: string) => {
          document.documentElement.dataset.copiedText = text;
        },
      },
    });
  });
}
async function open(page: Page, id: string) {
  await page.goto(`/?job=${id}`);
  await expect(page.locator("#filename")).not.toBeEmpty();
}
for (const [format, id] of [
  ["PPTX", slide],
  ["XLSX", sheet],
  ["PDF", pdf],
]) {
  test(`${format} viewer document menu download and print preview`, async ({
    page,
  }) => {
    await open(page, id);
    if (format === "PDF") {
      // The original frame and its controls finish loading asynchronously.
      await expect(page.locator("#explanationDisclosure")).toHaveCount(0);
      await waitForViewerSurface(page);
      await waitForViewerSurface(page);
      await expect(
        page.frameLocator("#original").locator("#pdfPage"),
      ).toHaveValue("1");
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
    }
    await page.getByLabel("文書メニュー", { exact: true }).click();
    await expect(page.locator("#saveMenu")).toHaveAttribute("open", "");
    await expect(page.locator("#saveMenu .menu-panel > *")).toHaveCount(2);
    await expect(page.locator("#originalDownload")).toHaveText(
      "原文ドキュメントをダウンロード",
    );
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.locator("#originalDownload").click(),
    ]);
    expect(download.suggestedFilename()).toBe(`QA.${format.toLowerCase()}`);
    await expect(page.locator("#saveMenu")).not.toHaveAttribute("open", "");
    await expect(page.locator("#saveMenu summary")).toBeFocused();
    await page.getByLabel("文書メニュー", { exact: true }).click();
    await page.locator("#printPreviewOpen").click();
    await expect(page.locator("#printPreviewDialog")).toBeVisible();
    await expect(page.locator("#saveMenu")).not.toHaveAttribute("open", "");
    await page
      .locator("#printPreviewDialog")
      .getByRole("button", { name: "閉じる", exact: true })
      .click();
    await expect(page.locator("#saveMenu summary")).toBeFocused();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page.locator("#printPreviewDialog")).toBeVisible();
    await expect(page.locator("#printPreviewFrame")).toHaveCSS(
      "pointer-events",
      "auto",
    );
    await page.keyboard.press("Escape");
    await expect(page.locator("#printPreviewDialog")).toBeHidden();
    await expect(page.locator("#saveMenu summary")).toBeFocused();
    await page.locator("#tab-structure").click();
    await page.getByLabel("文書メニュー", { exact: true }).click();
    await expect(page.locator("#printPreviewOpen")).toBeEnabled();
    if (format === "PDF") {
      await page.screenshot({ path: "../qa/viewer-menu/menu-desktop.png" });
      await page.getByLabel("文書メニュー", { exact: true }).click();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByLabel("文書メニュー", { exact: true }).click();
      await expect(page.locator("#saveMenu")).toHaveAttribute("open", "");
      await expect(page.locator("#originalDownload")).toBeVisible();
      await expect(page.locator("#printPreviewOpen")).toBeVisible();
      const menu = (await page.locator("#saveMenu .menu-panel").boundingBox())!;
      expect(menu.x).toBeGreaterThanOrEqual(0);
      expect(menu.x + menu.width).toBeLessThanOrEqual(390);
      expect(
        await page.locator("#printPreviewOpen").evaluate((button) => {
          const bounds = button.getBoundingClientRect();
          return button.contains(
            document.elementFromPoint(
              bounds.x + bounds.width / 2,
              bounds.y + bounds.height / 2,
            ),
          );
        }),
      ).toBe(true);
      await page.screenshot({ path: "../qa/viewer-menu/menu-mobile.png" });
    }
  });
}
test("viewer menu Escape returns keyboard focus to its trigger", async ({
  page,
}) => {
  await open(page, pdf);
  const trigger = page.locator("#saveMenu summary");
  await trigger.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.locator("#printPreviewOpen")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#saveMenu")).not.toHaveAttribute("open", "");
  await expect(trigger).toBeFocused();
});
for (const unavailable of ["processing", "missing preview"]) {
  test(`viewer menu keeps original download available with ${unavailable}`, async ({
    page,
  }) => {
    await page.route("**/api/library", async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      const job = data.jobs.find((item: { id: string }) => item.id === pdf);
      job.preview = null;
      if (unavailable === "processing") job.state = "running";
      await route.fulfill({ response, json: data });
    });
    await open(page, pdf);
    if (unavailable === "missing preview")
      await expect(page.locator("#noPreview")).toContainText("「…」メニュー");
    await page.getByLabel("文書メニュー", { exact: true }).click();
    await expect(page.locator("#saveMenu .menu-panel > *")).toHaveCount(2);
    if (unavailable === "processing")
      await expect(page.locator("#printPreviewOpen")).toBeDisabled();
    else await expect(page.locator("#printPreviewOpen")).toBeEnabled();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.locator("#originalDownload").click(),
    ]);
    expect(download.suggestedFilename()).toBe("QA.pdf");
    await expect(page.locator("#saveMenu summary")).toBeFocused();
  });
}
test("PPTX preview text copy starts in glyph gaps and clears on slide reselection", async ({
  page,
}) => {
  await capturePreviewClipboard(page);
  // The saved SVG deck includes individually positioned glyphs and background
  // paths. The Quick Look smoke fixture alone cannot reproduce this regression.
  await open(page, "a".repeat(32));
  // Wait for the page glyphs before measuring selection coordinates.
  await expect(page.locator("#explanationDisclosure")).toHaveCount(0);
  await waitForViewerSurface(page);
  const preview = page.frameLocator("#slideCanvas iframe");
  const text = preview.locator("text").first();
  await expect(text).toBeVisible();
  await expect(text).toHaveCSS("pointer-events", "bounding-box");
  const bounds = (await text.boundingBox())!;
  await page.mouse.move(bounds.x + 3, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 200, bounds.y + bounds.height / 2, {
    steps: 20,
  });
  await page.mouse.up();
  const selected = await text.evaluate(() => getSelection()!.toString());
  expect(selected).toContain("PT.");
  await expect(page.locator("#previewCopy")).toBeEnabled();
  await page.locator("#previewCopy").click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-copied-text",
    selected,
  );
  await page.getByRole("button", { name: "スライド 1", exact: true }).click();
  await expect(page.locator("#previewCopy")).toBeDisabled();
  await page.locator("#slideNext").click();
  await expect(page.locator("#previewCopy")).toBeDisabled();
});
test("XLSX preview text copy selects cells, preserves drags and clears on sheet switch", async ({
  page,
}) => {
  await capturePreviewClipboard(page);
  await open(page, sheet);
  const workbook = page.frameLocator("#original");
  const preview = workbook.frameLocator("#sheet-1");
  await workbook.locator("#sheetActualSize").click();
  const cell = preview
    .locator("td")
    .filter({ hasText: /^区分$/ })
    .first();
  await cell.click();
  await expect(page.locator("#previewCopy")).toBeEnabled();
  await page.locator("#previewCopy").click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-copied-text",
    "区分",
  );
  const number = preview.locator("td").filter({ hasText: /^120$/ }).first();
  await number.click();
  await page.locator("#previewCopy").click();
  await expect(page.locator("html")).toHaveAttribute("data-copied-text", "120");
  // Use native double-click/drag selection, rather than creating a DOM range.
  await cell.dblclick();
  await expect(page.locator("#previewCopy")).toBeEnabled();
  const bounds = (await cell.boundingBox())!;
  await page.mouse.move(bounds.x + 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width - 2,
    bounds.y + bounds.height / 2,
    {
      steps: 20,
    },
  );
  await page.mouse.up();
  const selected = await cell.evaluate(() => getSelection()!.toString());
  expect(selected).toContain("区分");
  await page.locator("#previewCopy").click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-copied-text",
    selected,
  );
  await workbook.locator("#sheet-tab-2").click();
  await expect(page.locator("#previewCopy")).toBeDisabled();
  await workbook.locator("#sheet-tab-1").click();
  await expect(page.locator("#previewCopy")).toBeDisabled();
});
test("bundle delivery, CSS order, Origin restrictions and legacy fallback", async ({
  page,
  request,
  baseURL,
}) => {
  const saved = await (await request.get("/api/library")).json();
  await page.goto("/?mode=library");
  await expect(page.locator("#jobs tr")).toHaveCount(saved.jobs.length);
  const links = await page
    .locator("link[rel=stylesheet]")
    .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("href")));
  expect(links.slice(0, 6)).toEqual([
    "/static/vendor/ag-grid.min.css",
    "/static/vendor/ag-theme-quartz.min.css",
    "/static/table-ui.css",
    "/static/style.css",
    "/static/translation.css",
    "/static/explanation.css",
  ]);
  expect(links.slice(6)).toEqual([
    expect.stringMatching(
      /^\/static\/frontend\/assets\/DocumentViewer-[\w-]+\.css$/,
    ),
    expect.stringMatching(/^\/static\/frontend\/assets\/index-[\w-]+\.css$/),
  ]);
  const bad = await request.post("/api/folders", {
    headers: { Origin: "http://elsewhere.example" },
    data: { name: "Rejected" },
  });
  expect(bad.status()).toBe(403);
  const good = await request.post("/api/folders", {
    headers: { Origin: baseURL! },
    data: { name: "Origin OK" },
  });
  expect(good.status()).toBe(201);
  const legacy = await request.get("/legacy/");
  expect(await legacy.text()).toContain("/static/library.js");
  const escape = await request.get("/static/frontend/%2E%2E/package.json");
  expect(escape.status()).toBe(404);
});
test("folder CRUD, search, multi-selection, move, copy and immediate delete", async ({
  page,
}) => {
  await page.goto("/?mode=library");
  await page.locator("#newFolder").click();
  await page.locator("#operationName").fill("React QA");
  await page.locator("#submitOperation").click();
  await expect(page.locator("#libraryDialog")).toBeHidden();
  await page.getByLabel("React QAを開く", { exact: true }).click();
  await expect(page.locator("#libraryEmpty")).toBeVisible();
  await page.locator("#breadcrumbs button").first().click();
  await page.locator("#fileSearch").fill("QA.");
  await expect(page.locator("#jobs tr")).toHaveCount(3);
  await page.getByLabel("QA.pptxを選択", { exact: true }).check();
  await page.getByLabel("QA.xlsxを選択", { exact: true }).check();
  await expect(page.locator("#selectionCount")).toHaveText("2件選択");
  await page.locator("#selectionCopy").click();
  await page
    .getByRole("button", { name: "React QA", exact: true })
    .last()
    .click();
  await page.locator("#submitOperation").click();
  await expect(page.locator("#libraryDialog")).toBeHidden();
  await page.locator("#fileSearch").fill("React QA");
  await page.getByLabel("React QAを開く", { exact: true }).click();
  await expect(page.locator("#jobs tr")).toHaveCount(2);
  await page.locator("#selectAll").check();
  await page.locator("#selectionMove").click();
  await page.locator("#destinationBreadcrumbs button").first().click();
  await page.locator("#submitOperation").click();
  await expect(page.locator("#libraryDialog")).toBeHidden();
  await expect(page.locator("#jobs tr")).toHaveCount(0);
  await page.locator("#breadcrumbs button").first().click();
  await page.locator("#fileSearch").fill("React QA");
  await page.getByLabel("React QAを選択", { exact: true }).check();
  await page.locator("#selectionRename").click();
  await page.locator("#operationName").fill("Renamed QA");
  await page.locator("#submitOperation").click();
  await expect(page.locator("#libraryDialog")).toBeHidden();
  await page.locator("#fileSearch").fill("Renamed QA");
  await page.getByLabel("Renamed QAを選択", { exact: true }).check();
  await page.locator("#selectionDelete").click();
  await expect(page.locator("#libraryDialog")).not.toBeVisible();
  await expect(page.locator("#jobs tr")).toHaveCount(0);
});
test("PPTX Fit, zoom, keyboard, thumbnail rail and retained frame across tabs", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await open(page, slide);
  await expect(page.locator("#slideCanvas iframe")).toBeVisible();
  await page.locator("#slideNext").click();
  await expect(page.locator("#slidePicker")).toHaveValue("2");
  await page.locator("#slideZoomPreset").selectOption("2");
  await expect(page.locator("#slideCanvas")).toHaveCSS(
    "transform",
    "matrix(2, 0, 0, 2, 0, 0)",
  );
  await page.evaluate(() => {
    document
      .querySelector("#slideCanvas iframe")!
      .setAttribute("data-identity", "retained");
  });
  await page.locator("#tab-rag").click();
  await expect(page.locator("#ragCards article")).not.toHaveCount(0);
  await page.locator("#tab-preview").click();
  await expect(page.locator("#slideCanvas iframe")).toHaveAttribute(
    "data-identity",
    "retained",
  );
  await expect(page.locator("#slidePicker")).toHaveValue("2");
  await page.locator("#slideFit").click();
  await page.locator("#slideStage").focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator("#slidePicker")).toHaveValue("1");
  await page.locator("#slideThumbnailsToggle").click();
  await expect(page.locator("#slideThumbnails")).toBeHidden();
  expect(errors).toEqual([]);
});
for (const [name, id] of [
  ["PDF", pdf],
  ["XLSX", sheet],
])
  test(`${name} position/zoom survives tabs, polling, and language changes`, async ({
    page,
  }) => {
    if (process.env.UI_TRACE_FRAME) {
      page.on("framenavigated", (frame) => console.log("FRAME", frame.url()));
      page.on("console", (message) => console.log("MESSAGE", message.text()));
      await page.addInitScript(() => {
        if (!location.pathname.endsWith("/workbook")) return;
        console.log("WORKBOOK INIT");
        window.addEventListener("message", (event) =>
          console.log("WORKBOOK MESSAGE", JSON.stringify(event.data)),
        );
        window.addEventListener("load", () => console.log("WORKBOOK LOAD"));
      });
    }
    await open(page, id);
    await expect(page.locator("#explanationDisclosure")).toHaveCount(0);
    await waitForViewerSurface(page);
    await waitForViewerSurface(page);
    const original = page.frameLocator("#original");
    await page
      .locator("#original")
      .evaluate((n) => n.setAttribute("data-identity", "retained"));
    if (name === "PDF") {
      await original.locator("#pdfPage").fill("2");
      await original.locator("#pdfPage").dispatchEvent("change");
      await original.locator("#pdfZoom").selectOption("200");
    } else {
      await original.locator("#sheet-tab-2").click();
      await expect(original.locator("#sheet-tab-2")).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await original.locator("#sheetActualSize").click();
    }
    await page.locator("#tab-structure").click();
    await expect(page.locator("#structure article")).not.toHaveCount(0);
    await page.locator("#tab-preview").click();
    await expect(page.locator("#original")).toHaveAttribute(
      "data-identity",
      "retained",
    );
    if (name === "PDF")
      await expect(original.locator("#pdfPage")).toHaveValue("2");
    else
      await expect(original.locator("#sheet-tab-2")).toHaveAttribute(
        "aria-selected",
        "true",
      );
    await page.locator("#translationLanguage").selectOption("en");
    await expect(page.locator("#translationStatus")).not.toContainText(
      "未翻訳",
    );
    await page.locator("#translationLanguage").selectOption("ja");
    await page.locator("#translationLanguage").selectOption("original");
  });
test("PDF keeps its page when scroll arrives before a hidden tab's resize restoration", async ({
  page,
}) => {
  await page.addInitScript(() => {
    if (!location.pathname.endsWith("/pdf")) return;
    const pending: (() => void)[] = [];
    const handlers: (() => void)[] = [];
    const state = {
      hold: false,
      hiddenObserved: false,
      pending: 0,
      release() {
        state.hold = false;
        pending.splice(0).forEach((callback) => callback());
      },
      // Not every browser delivers ResizeObserver callbacks to a frame that is
      // display:none, so the test delivers them itself to model engines that do.
      deliver() {
        handlers.forEach((handler) => handler());
      },
    };
    Object.assign(window, { pdfResizeTest: state });
    const NativeObserver = window.ResizeObserver;
    window.ResizeObserver = class extends NativeObserver {
      constructor(callback: ResizeObserverCallback) {
        const handle = (
          entries: ResizeObserverEntry[],
          observer: ResizeObserver,
        ) => {
          const stage = document.getElementById("pdfStage");
          if (!stage?.clientWidth) state.hiddenObserved = true;
          if (stage?.clientWidth && state.hold) {
            pending.push(() => callback(entries, observer));
            state.pending = pending.length;
          } else callback(entries, observer);
        };
        super(handle);
        handlers.push(() => handle([], this));
      }
    };
  });
  await open(page, pdf);
  await expect(page.locator("#explanationDisclosure")).toHaveCount(0);
  await waitForViewerSurface(page);
  const original = page.frameLocator("#original");
  const stage = original.locator("#pdfStage");
  await original.locator("#pdfPage").fill("2");
  await original.locator("#pdfPage").dispatchEvent("change");
  await original.locator("#pdfZoom").selectOption("200");
  await expect(original.locator("#pdfPage")).toHaveValue("2");
  await page.locator("#tab-structure").click();
  await expect
    .poll(() =>
      stage.evaluate(() => {
        const w = window as unknown as Window & {
          pdfResizeTest: {
            hold: boolean;
            hiddenObserved: boolean;
            deliver: () => void;
          };
        };
        w.pdfResizeTest.hold = true;
        w.pdfResizeTest.deliver();
        return w.pdfResizeTest.hiddenObserved;
      }),
    )
    .toBe(true);
  await page.locator("#tab-preview").click();
  await expect
    .poll(() =>
      stage.evaluate(() => {
        const w = window as unknown as Window & {
          pdfResizeTest: { pending: number; deliver: () => void };
        };
        w.pdfResizeTest.deliver();
        return w.pdfResizeTest.pending;
      }),
    )
    .toBeGreaterThan(0);
  await stage.evaluate(async (node) => {
    node.scrollTop = 0;
    node.dispatchEvent(new Event("scroll"));
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  });
  await expect(original.locator("#pdfPage")).toHaveValue("2");
  await stage.evaluate(() => {
    (
      window as unknown as Window & { pdfResizeTest: { release: () => void } }
    ).pdfResizeTest.release();
  });
  await expect(original.locator("#pdfPage")).toHaveValue("2");
  await expect(original.locator("#pdfZoom option:checked")).toHaveText("200%");
  await expect
    .poll(() => stage.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
});

test("saved explanations and translations reopen without a generation POST", async ({
  page,
}) => {
  let posts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && /translations|explanations/.test(r.url()))
      posts++;
  });
  await open(page, slide);
  await expect(page.locator("#explainOpen")).toBeEnabled();
  await page.locator("#explainOpen").click();
  await expect(page.locator("#explanationText h3")).not.toHaveCount(0);
  await page.locator("#explainClose").click();
  await page.locator("#explainOpen").click();
  await expect(page.locator("#explanationText h3")).not.toHaveCount(0);
  await page.locator("#translationLanguage").selectOption("en");
  await expect(
    page.frameLocator("#slideCanvas iframe").locator("body"),
  ).toContainText("EN ");
  expect(posts).toBe(0);
});
test("translation submit has one POST, selected units, force and pacing", async ({
  page,
}) => {
  await open(page, slide);
  // Measure dialog interactions after the source frame has loaded, rather
  // than while Chrome is still committing a newly created iframe surface.
  await expect(
    page.frameLocator("#slideCanvas iframe").locator("body"),
  ).toContainText("合成サンプル");
  await waitForViewerSurface(page);
  await page.locator("#translateOpen").click();
  await expect(page.locator("#slideCanvas iframe")).toHaveCSS(
    "pointer-events",
    "none",
  );
  await page.locator("#translationScope").selectOption("selected");
  await page.locator("#translationUnitList input").first().uncheck();
  await page.locator("#translationSubmit").click();
  await expect(page.locator("#translationDialogMessage")).toContainText("範囲");
  await page.locator("#translationUnitList input").first().check();
  await page.locator("#translationForce").check();
  await page.locator("#translationInterval").fill("0");
  const received = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith("/translations"),
  );
  await page.locator("#translationSubmit").click();
  const body = (await received).postDataJSON();
  expect(body.unit_ids).toEqual(["slide-1"]);
  expect(body.force).toBe(true);
  expect(body.interval_seconds).toBe(0);
  await expect(page.locator("#translationDialog")).toBeHidden();
  await expect(page.locator("#slideCanvas iframe")).toHaveCSS(
    "pointer-events",
    "auto",
  );
});
test("explanation polling recovers after failure and never restarts saved generation", async ({
  page,
}) => {
  let gets = 0,
    posts = 0;
  await page.route("**/explanations", async (route) => {
    if (route.request().method() === "POST") {
      posts++;
      await route.continue();
      return;
    }
    if (++gets === 1) {
      await route.fulfill({ status: 503, json: { detail: "QA disconnected" } });
      return;
    }
    await route.continue();
  });
  await open(page, slide);
  await expect(page.locator("#explainOpen")).toBeEnabled();
  await page.locator("#explainOpen").click();
  await expect(page.locator("#explanationText h3")).not.toHaveCount(0);
  expect(posts).toBe(0);
});
test("table read-only selection, range clipboard, filtering, menus, expand and source", async ({
  page,
}) => {
  await open(page, sheet);
  await page.locator("#tab-tables").click();
  const card = page.locator("#tableGrid .table-card:visible");
  await expect(card.locator(".ag-cell").first()).toBeVisible();
  const cells = card.locator('.ag-row[row-index="0"] .ag-cell');
  await cells.first().click();
  await cells.nth(1).click({ modifiers: ["Shift"] });
  await expect(card.locator(".table-cell-address")).toContainText("2列");
  await card.getByRole("button", { name: "選択をコピー" }).click();
  await card.locator(".table-column-menu").first().click();
  await page.getByRole("menuitem", { name: "降順に並べ替え" }).click();
  await expect(card.locator(".table-conditions")).toContainText("降順");
  await card.getByRole("button", { name: "絞り込み", exact: true }).click();
  await page
    .getByLabel("絞り込み条件", { exact: true })
    .selectOption("notempty");
  await page.getByRole("button", { name: "適用", exact: true }).click();
  await expect(card.locator(".table-conditions")).toContainText("空欄以外");
  await card.getByRole("button", { name: "広く表示" }).click();
  await expect(page.locator("dialog.table-focus")).toBeVisible();
  await expect(
    page.locator("dialog.table-focus .ag-cell").first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "文書に戻る", exact: true }).click();
  await expect(card.locator(".table-conditions")).toContainText("降順");
  await card.getByRole("button", { name: "出典を見る" }).click();
  await page
    .getByRole("button", { name: "原本プレビューを見る", exact: true })
    .click();
  await expect(page.locator("#preview")).toBeVisible();
});
test("URL history, unknown ID and stale cross-document responses", async ({
  page,
}) => {
  await page.goto("/?mode=library");
  await page.getByLabel("QA.pptxを開く", { exact: true }).first().click();
  await page.locator("#tab-structure").click();
  await page.reload();
  await expect(page.locator("#structure")).toBeVisible();
  await page.locator("#backToFiles").click();
  await expect(page.locator("#library")).toBeVisible();
  await page.goBack();
  await expect(page.locator("#detail")).toBeVisible();
  await page.goto("/?job=missing");
  await expect(page.getByRole("status")).toContainText([
    "資料が見つかりません。",
  ]);
  await page.locator("#backToFiles").click();
  await expect(page.locator("#library")).toBeVisible();
});
test("file drop and partial upload failures retain a fixed destination", async ({
  page,
}) => {
  await page.goto("/?mode=library");
  let sent = 0;
  await page.route("**/api/upload", async (route) => {
    sent++;
    await route.fulfill({
      status: sent === 2 ? 429 : 202,
      json: sent === 2 ? { detail: "QA full queue" } : { id: "uploaded" },
    });
  });
  await page.locator("#addFile").click();
  await page.locator("#upload input[type=file]").setInputFiles([
    {
      name: "one.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF"),
    },
    {
      name: "two.xlsx",
      mimeType: "application/octet-stream",
      buffer: Buffer.from("zip"),
    },
  ]);
  await page.locator("#upload button[type=submit]").click();
  await expect(page.locator("#uploadMessage")).toContainText("1件の資料を追加");
  await expect(page.locator("#uploadMessage")).toContainText("QA full queue");
  expect(sent).toBe(2);
  await page.getByRole("button", { name: "閉じる", exact: true }).click();
  const transfer = await page.evaluateHandle(() => {
    const d = new DataTransfer();
    d.items.add(new File(["content"], "drop.pdf", { type: "application/pdf" }));
    return d;
  });
  await page.locator("body").dispatchEvent("drop", { dataTransfer: transfer });
  await expect.poll(() => sent).toBe(3);
});
test("unknown frame messages cannot change current unit or upload", async ({
  page,
}) => {
  await open(page, slide);
  let posts = 0;
  page.on("request", (r) => {
    if (r.url().includes("/api/upload") && r.method() === "POST") posts++;
  });
  await page.evaluate((id) => {
    window.postMessage(
      { type: "docling-unit-current", jobId: id, number: 2 },
      "*",
    );
    window.postMessage(
      {
        type: "docling-file-drop",
        action: "drop",
        files: [new File(["x"], "fake.pdf")],
        rejected: [],
      },
      "*",
    );
  }, slide);
  await expect(page.locator("#slidePicker")).toHaveValue("1");
  expect(posts).toBe(0);
});

test("uncreated explanation starts once and then reopens its saved result", async ({
  page,
}) => {
  let posts = 0;
  await page.route(`**/api/jobs/${slide}/explanations`, async (route) => {
    if (route.request().method() === "POST") {
      posts++;
      await route.fulfill({ status: 202, json: { accepted: true } });
      return;
    }
    const response = await route.fetch();
    const data = await response.json();
    if (!posts) {
      data.units[0].available = false;
      data.units[0].state.state = "uncreated";
      data.units[0].state.latest_version_id = null;
    }
    await route.fulfill({ response, json: data });
  });
  await open(page, slide);
  await expect(page.locator("#explainOpen")).toBeEnabled();
  await page.locator("#explainOpen").evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect(page.locator("#explanationText h3")).not.toHaveCount(0);
  expect(posts).toBe(1);
  await page.locator("#explainClose").click();
  await page.locator("#explainOpen").click();
  await expect(page.locator("#explanationText h3")).not.toHaveCount(0);
  expect(posts).toBe(1);
});

test("late responses from document A cannot enter document B", async ({
  page,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  await page.route(`**/api/jobs/${slide}/explanations`, async (route) => {
    started = true;
    await gate;
    await route
      .fulfill({ status: 503, json: { detail: "Document A stale error" } })
      .catch(() => {});
  });
  await open(page, slide);
  await expect.poll(() => started).toBe(true);
  await page.locator("#backToFiles").click();
  await page.getByLabel("QA.pdfを開く", { exact: true }).click();
  await expect(page.locator("#filename")).toHaveText("QA.pdf");
  release();
  await expect(page.locator("#explainOpen")).toBeEnabled();
  await page.locator("#explainOpen").click();
  await expect(page.locator("#explanationText h3")).not.toHaveCount(0);
  await expect(page.locator("#explanationStatus")).not.toContainText(
    "Document A stale error",
  );
});

for (const action of ["close", "change-slide", "leave-document"] as const) {
  test(`pending explanation does not generate after ${action}`, async ({
    page,
  }) => {
    let hold = false,
      started = false,
      posts = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/jobs/${slide}/explanations`, async (route) => {
      if (route.request().method() === "POST") {
        posts++;
        await route.fulfill({ json: { accepted: true } });
        return;
      }
      const response = await route.fetch();
      const data = await response.json();
      data.units.forEach(
        (unit: {
          available: boolean;
          state: { state: string; latest_version_id: string | null };
        }) => {
          unit.available = false;
          unit.state.state = "uncreated";
          unit.state.latest_version_id = null;
        },
      );
      if (hold) {
        started = true;
        await gate;
      }
      await route.fulfill({ response, json: data }).catch(() => {});
    });
    await open(page, slide);
    await expect(page.locator("#explainOpen")).toBeEnabled();
    hold = true;
    await page.locator("#explainOpen").click();
    await expect.poll(() => started).toBe(true);
    if (action === "close") await page.locator("#explainClose").click();
    else if (action === "change-slide")
      await page.locator("#slidePicker").selectOption("2");
    else await page.locator("#backToFiles").click();
    hold = false;
    release();
    await page.waitForTimeout(250);
    expect(posts).toBe(0);
    if (action !== "leave-document") {
      await expect(page.locator("#explainOpen")).toBeEnabled();
      await page.locator("#explainOpen").click();
      await expect.poll(() => posts).toBe(1);
    }
  });
}

test("cached PPTX thumbnails finish after reopening the viewer", async ({
  page,
}) => {
  await open(page, slide);
  await expect(page.locator("#slideThumbnails img").first()).toBeVisible();
  await page.locator("#backToFiles").click();
  await page.route(`**/api/jobs/${slide}/slides/*/thumbnail`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.continue().catch(() => {});
  });
  await page.getByLabel("QA.pptxを開く", { exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator("#slideThumbnails img")
        .evaluateAll((images) =>
          images.every(
            (image) =>
              image instanceof HTMLImageElement &&
              image.complete &&
              image.naturalWidth > 0,
          ),
        ),
    )
    .toBe(true);
  await expect(page.locator("#slideThumbnails .loading")).toHaveCount(0);
});

test("saved PPTX language changes navigate the source frame once", async ({
  page,
}) => {
  let navigations = 0;
  page.on("request", (request) => {
    if (request.url().includes(`/view/${slide}/slides/1?language=ja`))
      navigations++;
  });
  await open(page, slide);
  await expect(page.locator("#explainOpen")).toBeEnabled();
  await page.locator("#translationLanguage").selectOption("ja");
  await expect(
    page.frameLocator("#slideCanvas iframe").locator("body"),
  ).toContainText("JA");
  await page.waitForTimeout(200);
  expect(navigations).toBe(1);
  await page.locator("#backToFiles").click();
  await page.getByLabel("QA.pptxを開く", { exact: true }).click();
  await expect(
    page.frameLocator("#slideCanvas iframe").locator("body"),
  ).toContainText("JA");
  await page.waitForTimeout(200);
  expect(navigations).toBe(2);
});

test("late clipboard failure cannot reopen a table popup after leaving that tab", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: () =>
          new Promise<void>((_resolve, reject) => {
            window.addEventListener(
              "review-copy-failure",
              () => reject(new Error("denied")),
              { once: true },
            );
          }),
      },
    });
  });
  await open(page, sheet);
  await page.locator("#tab-tables").click();
  await page.locator("#tableGrid .ag-cell").first().click();
  await page
    .getByRole("button", { name: "選択をコピー", exact: true })
    .first()
    .click();
  await page.locator("#tab-preview").click();
  await expect(page.locator("#original")).toBeVisible();
  await page.evaluate(() =>
    window.dispatchEvent(new Event("review-copy-failure")),
  );
  await page.waitForTimeout(200);
  await expect(page.locator(".table-popup")).toHaveCount(0);
});

test("malformed library dates become a recoverable status instead of a blank screen", async ({
  page,
}) => {
  await page.route("**/api/library", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.jobs[0].created = null;
    await route.fulfill({ response, json: data });
  });
  await page.goto("/?mode=library");
  await expect(page.locator("#message")).toContainText("応答形式");
  await expect(page.locator("#addFile")).toBeVisible();
  await page.unroute("**/api/library");
  await expect(page.locator("#jobs tr").first()).toBeVisible();
  await expect(page.locator("#message")).toBeHidden();
});

test("passive fallback previews allow the parent to receive file drops", async ({
  page,
}) => {
  let uploads = 0;
  await page.route(`**/files/${slide}/slides.json`, (route) =>
    route.fulfill({ status: 503, json: { detail: "metadata unavailable" } }),
  );
  await page.route("**/api/upload", (route) => {
    uploads++;
    return route.fulfill({ json: { accepted: true } });
  });
  await open(page, slide);
  await expect(page.locator("#original")).toBeVisible();
  await expect(page.locator("#original")).toHaveCSS("pointer-events", "none");
  await page.locator("#original").evaluate((frame) => {
    const rect = frame.getBoundingClientRect();
    const target = document.elementFromPoint(
      rect.x + rect.width / 2,
      rect.y + rect.height / 2,
    )!;
    if (target.tagName === "IFRAME")
      throw new Error("passive frame intercepted the drop");
    const transfer = new DataTransfer();
    transfer.items.add(
      new File(["%PDF-1.4"], "fallback.pdf", { type: "application/pdf" }),
    );
    target.dispatchEvent(
      new DragEvent("drop", {
        bubbles: true,
        cancelable: true,
        dataTransfer: transfer,
      }),
    );
  });
  await expect.poll(() => uploads).toBe(1);
});

test("a missing table chunk keeps document navigation available", async ({
  page,
}) => {
  test.skip(
    !!process.env.UI_DEV,
    "Development modules do not use the production table chunk.",
  );
  await page.route("**/assets/Tables-*.js", (route) => route.abort());
  await open(page, sheet);
  await page.locator("#tab-tables").click();
  await expect(page.getByRole("alert")).toContainText(
    "表の画面を読み込めませんでした",
  );
  await expect(
    page.getByRole("button", { name: "再読み込み", exact: true }),
  ).toBeVisible();
  await page.locator("#tab-preview").click();
  await expect(page.locator("#original")).toBeVisible();
  await page.locator("#backToFiles").click();
  await expect(page.locator("#library")).toBeVisible();
  // React logs the deliberately caught import failure. No other warning is
  // allowed to pass the normal diagnostic assertion.
  const issues = diagnostics.get(page)!;
  for (const issue of issues) expect(issue).toContain("FeatureBoundary");
  issues.length = 0;
});

test("thumbnail requests stay bounded across rapid tab and rail toggles", async ({
  page,
}) => {
  let active = 0,
    maximum = 0;
  await page.route("**/slides/*/thumbnail", async (route) => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, 150));
    const response = await route.fetch();
    await route.fulfill({ response });
    active--;
  });
  await open(page, "a".repeat(32));
  await expect(page.locator("#slideCanvas iframe")).toBeVisible();
  await page.locator("#tab-structure").click();
  await page.locator("#tab-preview").click();
  await page.locator("#slideThumbnailsToggle").click();
  await page.locator("#slideThumbnailsToggle").click();
  await expect(page.locator("#slideThumbnails img[src]").first()).toBeVisible();
  await expect.poll(() => active).toBe(0);
  expect(maximum).toBeLessThanOrEqual(2);
});

test("viewer file drops upload once and retain the dialog's original folder", async ({
  page,
  request,
}) => {
  const created = await (
    await request.post("/api/folders", { data: { name: "Upload target QA" } })
  ).json();
  const id = created.id;
  await page.goto(`/?folder=${id}`);
  await expect(page.locator("#breadcrumbs")).toContainText("Upload target QA");
  await page.locator("#addFile").click();
  await page.evaluate(() => {
    history.pushState({}, "", "/?mode=library");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  const bodies: string[] = [];
  await page.route("**/api/upload", async (route) => {
    bodies.push(route.request().postDataBuffer()!.toString());
    await route.fulfill({ status: 202, json: { id: "uploaded" } });
  });
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(
      new File(["%PDF"], "fixed.pdf", { type: "application/pdf" }),
    );
    return data;
  });
  await page
    .locator("#uploadDialog")
    .dispatchEvent("drop", { dataTransfer: transfer });
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0]).toContain(id);
  await expect(page.locator("#uploadDialog")).toBeHidden();
  await open(page, pdf);
  await expect(
    page.frameLocator("#original").locator("#pdfStage"),
  ).toBeVisible();
  const viewer = page
    .frames()
    .find((frame) => frame.url().endsWith(`/view/${pdf}/pdf`))!;
  const file = await viewer.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(
      new File(["%PDF"], "viewer.pdf", { type: "application/pdf" }),
    );
    return data;
  });
  await viewer.locator("body").dispatchEvent("drop", { dataTransfer: file });
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toContain('filename="viewer.pdf"');
});

test("production cold-load measurements and lazy table bundle", async ({
  page,
}) => {
  test.skip(
    !!process.env.UI_DEV,
    "Cold-load asset measurements target the distributed production build.",
  );
  const measurements = [];
  for (const path of ["/legacy/", "/ui/"]) {
    const calls: Record<string, number> = {},
      assets: string[] = [],
      thumbnails: string[] = [];
    const collect = (r: import("@playwright/test").Request) => {
      const u = new URL(r.url());
      if (u.pathname.startsWith("/api/"))
        calls[u.pathname] = (calls[u.pathname] || 0) + 1;
      if (u.pathname.endsWith(".js")) assets.push(u.pathname);
      if (
        u.pathname.includes("/thumbnails/") ||
        u.pathname.endsWith("/thumbnail")
      )
        thumbnails.push(u.pathname);
    };
    page.on("request", collect);
    const session = await page.context().newCDPSession(page);
    await session.send("Network.clearBrowserCache");
    await session.detach();
    const started = Date.now();
    await page.goto(`${path}?job=${slide}`);
    await expect(page.locator("#slideCanvas iframe")).toBeVisible();
    const elapsed = Date.now() - started;
    await page.waitForTimeout(500);
    await expect(page.locator("#slideCanvas iframe")).toHaveCount(1);
    const initial = {
      apiCalls: { ...calls },
      scriptAssets: [...assets],
      thumbnailRequests: [...thumbnails],
      documentFrames: await page.locator("#slideCanvas iframe").count(),
    };
    if (path === "/ui/") {
      expect(assets.some((url) => /Tables-/.test(url))).toBe(false);
      await page.locator("#tab-tables").click();
      await expect(page.locator("#tableGrid .ag-cell").first()).toBeVisible();
      expect(assets.some((url) => /Tables-/.test(url))).toBe(true);
    }
    page.off("request", collect);
    measurements.push({
      path,
      millisecondsToSlideFrame: elapsed,
      initial,
      apiCalls: calls,
      scriptAssets: assets,
    });
  }
  const { writeFile } = await import("node:fs/promises");
  await writeFile(
    new URL("../../qa/frontend-migration/performance.json", import.meta.url),
    JSON.stringify(
      { browser: page.context().browser()?.version(), measurements },
      null,
      2,
    ),
  );
});
