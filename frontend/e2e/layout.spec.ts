import { test, expect, type Page, type Frame } from "@playwright/test";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";
const out = new URL(
  process.env.UI_LAYOUT_OUT || "../../qa/frontend-migration/layout/",
  import.meta.url,
);
const baselinePath = process.env.UI_BASELINE_PATH || "/legacy/";
interface Job {
  id: string;
  filename: string;
  pages: number;
  slide_layout: boolean;
  preview: string | null;
}
interface Case {
  job: Job;
  number: number;
  language: string;
  name: string;
  width: number;
  height: number;
  zoom?: number;
  panel?: boolean;
  rail?: boolean;
}
interface NativeSvg {
  box: { x: number; y: number; width: number; height: number };
  markup: string;
  style: { color: string; stroke: string; strokeWidth: string };
}
async function nativeSvg(page: Page): Promise<NativeSvg | null> {
  const icon = page
    .frameLocator("#original")
    .locator("#pdfThumbnailsToggle svg");
  if (!(await icon.count()) || !(await icon.isVisible())) return null;
  const box = await icon.boundingBox();
  if (!box) return null;
  return {
    box,
    ...(await icon.evaluate((e) => {
      const style = getComputedStyle(e);
      return {
        markup: e.outerHTML,
        style: {
          color: style.color,
          stroke: style.stroke,
          strokeWidth: style.strokeWidth,
        },
      };
    })),
  };
}
async function stableFrame(frame: Frame) {
  if (frame.isDetached()) return;
  try {
    await frame.waitForLoadState("load");
    await frame.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all(
        Array.from(document.images)
          .filter((i) => i.src && !i.closest("[hidden]"))
          .map((i) => i.decode().catch(() => {})),
      );
    });
  } catch (error) {
    if (
      !frame.isDetached() &&
      !/Execution context was destroyed/.test(String(error))
    )
      throw error;
  }
}
async function ready(page: Page) {
  for (const f of page.frames())
    if (f.url() !== "about:blank") await stableFrame(f);
  await page.evaluate(
    () =>
      new Promise<void>((r) =>
        requestAnimationFrame(() => requestAnimationFrame(() => r())),
      ),
  );
}
async function capture(page: Page) {
  return page.screenshot({
    animations: "disabled",
    caret: "hide",
    scale: "css",
  });
}
async function prepare(page: Page, path: string, c: Case) {
  await page.setViewportSize({ width: c.width, height: c.height });
  const session = await page.context().newCDPSession(page);
  await session.send("Network.clearBrowserCache");
  await session.detach();
  await page.goto(`${path}?job=${c.job.id}`);
  await expect(page.locator("#filename")).toHaveText(c.job.filename);
  // Initial metadata changes the available height. Apply the same user actions
  // only after that disclosure and the initial frame have finished loading.
  await expect(page.locator("#explanationDisclosure")).toContainText(
    "未作成の解説",
  );
  await ready(page);
  if (c.job.slide_layout) {
    await expect(page.locator("#slideCanvas iframe")).toBeVisible();
    await page.locator("#slidePicker").selectOption(String(c.number));
    if (c.rail === false) await page.locator("#slideThumbnailsToggle").click();
    if (c.zoom)
      await page.locator("#slideZoomPreset").selectOption(String(c.zoom));
  } else {
    await expect(page.locator("#original")).toBeVisible();
    const f = page.frameLocator("#original");
    if (c.job.filename.endsWith(".xlsx")) {
      await f.locator(`#sheet-tab-${c.number}`).click();
      await expect(f.locator(`#sheet-${c.number}`)).toBeVisible();
      await ready(page);
      await f.locator("#sheetActualSize").click();
      await ready(page);
      await page.waitForTimeout(200);
      const workbook = page
        .frames()
        .find((frame) => frame.url().endsWith(`/view/${c.job.id}/workbook`));
      const sheet = workbook
        ?.childFrames()
        .find((frame) => frame.url().includes(`/sheets/${c.number}`));
      if (sheet)
        await writeFile(
          new URL(
            `${c.name}-${path === baselinePath ? "legacy" : "ui"}-natural.json`,
            out,
          ),
          JSON.stringify(
            await sheet.evaluate(() => ({
              zoom: document.documentElement.style.zoom,
              table: document
                .querySelector("table")
                ?.getBoundingClientRect()
                .toJSON(),
              height: document.documentElement.clientHeight,
            })),
            null,
            2,
          ),
        );
      await sheet?.evaluate(() => window.dispatchEvent(new Event("resize")));
      await page.waitForTimeout(100);
      if (c.zoom && c.zoom !== 1) {
        for (let step = 0; step < 3; step++)
          await f.locator("#sheetZoomIn").click();
      } else if (c.zoom !== 1) await f.locator("#sheetFit").click();
      if (c.rail === false) {
        /* Sheet tabs are always retained. */
      }
    } else if (c.job.filename.endsWith(".pdf")) {
      await expect(f.locator("#page-1 iframe")).toHaveAttribute(
        "src",
        /\/view\//,
      );
      await ready(page);
      await page.waitForTimeout(150);
      await f.locator("#pdfWidth").click();
      await page.waitForTimeout(100);
      await f.locator("#pdfPage").fill(String(c.number));
      await f.locator("#pdfPage").dispatchEvent("change");
      if (c.zoom)
        await f.locator("#pdfZoom").selectOption(String(c.zoom * 100));
      if (c.rail === false) await f.locator("#pdfThumbnailsToggle").click();
      await expect(f.locator(`#page-${c.number} iframe`)).toHaveAttribute(
        "src",
        /\/view\//,
      );
    }
  }
  if (c.language !== "original") {
    await page.locator("#translationLanguage").selectOption(c.language);
    if (c.job.slide_layout)
      await expect(page.locator("#slideCanvas iframe")).toHaveAttribute(
        "src",
        new RegExp(`language=${c.language}`),
      );
    else await page.waitForTimeout(200);
  }
  if (c.job.slide_layout) {
    await expect
      .poll(async () => {
        const source = await page
          .locator("#slideCanvas iframe")
          .getAttribute("src");
        return (
          !!source &&
          page
            .frames()
            .some((f) => f.url() === new URL(source, page.url()).href)
        );
      })
      .toBe(true);
  }
  if (c.language !== "original" && c.job.filename.endsWith(".pdf")) {
    await expect
      .poll(() =>
        page
          .frames()
          .some((f) =>
            f.url().includes(`/pages/${c.number}?language=${c.language}`),
          ),
      )
      .toBe(true);
  }
  if (c.panel) {
    await expect(page.locator("#explainOpen")).toBeEnabled();
    await page.locator("#explainOpen").click();
    await expect(page.locator("#explanationText h3").first()).toBeVisible();
  }
  await ready(page);
  // Layout and transforms must settle before capture, including native viewer messaging.
  await page.waitForTimeout(150);
  await ready(page);
  // Remove keyboard focus exactly as for the old reference; do not mask document pixels.
  await page.locator("#filename").click();
  // Flush the browser viewport before recording geometry, including its first headless paint.
  await capture(page);
  await ready(page);
  let last = "",
    stable = 0;
  for (let attempt = 0; attempt < 80 && stable < 5; attempt++) {
    await ready(page);
    const now = JSON.stringify(await measure(page));
    stable = now === last ? stable + 1 : 0;
    last = now;
    await page.waitForTimeout(100);
  }
  expect(stable, `stable frame geometry: ${c.name}`).toBe(5);
  await capture(page);
  await ready(page);
  await page.waitForTimeout(100);
  if (c.job.slide_layout && c.rail !== false) {
    await expect
      .poll(() =>
        page.evaluate(() => {
          const rail = document.getElementById("slideThumbnails")!;
          const bounds = rail.getBoundingClientRect();
          return Array.from(rail.querySelectorAll("img"))
            .filter((img) => {
              const r = img.getBoundingClientRect();
              return r.bottom > bounds.top && r.top < bounds.bottom;
            })
            .every((img) => !!img.src && img.complete && img.naturalWidth > 0);
        }),
      )
      .toBe(true);
    await ready(page);
  }
}
async function measure(page: Page) {
  const parent = await page.evaluate(() => {
    const rect = (e: Element) => {
      const b = e.getBoundingClientRect();
      return [b.x, b.y, b.width, b.height].map((n) => Number(n.toFixed(3)));
    };
    const regions = Object.fromEntries(
      [
        "viewerToolbar",
        "explanationDisclosure",
        "documentPanels",
        "preview",
        "original",
        "slides",
        "slideThumbnails",
        "slideStage",
        "slideSurface",
        "slideCanvas",
        "translationPanel",
        "explanationPanel",
      ].map((id) => {
        const e = document.getElementById(id);
        return [
          id,
          e && e.getBoundingClientRect().width
            ? { rect: rect(e), transform: getComputedStyle(e).transform }
            : null,
        ];
      }),
    );
    const rail = document.getElementById("slideThumbnails"),
      bounds = rail?.getBoundingClientRect();
    return {
      ...regions,
      thumbnailItems: bounds?.width
        ? Array.from(rail!.querySelectorAll<HTMLButtonElement>("button"))
            .filter((button) => {
              const r = button.getBoundingClientRect();
              return r.bottom > bounds.top && r.top < bounds.bottom;
            })
            .map((button) => {
              const image = button.querySelector("img")!;
              return {
                number: button.dataset.page,
                current: button.getAttribute("aria-current"),
                button: rect(button),
                image: rect(image),
                source: image.src,
                naturalSize: [image.naturalWidth, image.naturalHeight],
              };
            })
        : null,
    };
  });
  const documents = [];
  for (const f of page.frames().slice(1)) {
    if (f.isDetached() || f.url() === "about:blank") continue;
    const element = await f.frameElement();
    if (!(await element.isVisible())) continue;
    documents.push({
      url: f.url(),
      ...(await f.evaluate(() => {
        const round = (n: number) => Number(n.toFixed(3)),
          rect = (r: DOMRect) => [r.x, r.y, r.width, r.height].map(round);
        const selectors =
          "#pdfStage,#pdfPages,.pdf-page,#sheetStage,#sheetTabs,table.worksheet";
        const regions = Array.from(document.querySelectorAll(selectors))
          .filter((e) => e.getBoundingClientRect().width)
          .map((e) => ({
            tag: e.id || e.className,
            rect: rect(e.getBoundingClientRect()),
            transform: getComputedStyle(e).transform,
          }));
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
          ),
          texts = [];
        let node: Node | null;
        while ((node = walker.nextNode()) && texts.length < 150) {
          if (
            !node.textContent?.trim() ||
            !node.parentElement ||
            ["SCRIPT", "STYLE"].includes(node.parentElement.tagName)
          )
            continue;
          const r = document.createRange();
          r.selectNodeContents(node);
          const lines = Array.from(r.getClientRects()).map(rect);
          if (!lines.length) continue;
          const s = getComputedStyle(node.parentElement);
          texts.push({
            text: node.textContent,
            lines,
            font: s.fontFamily,
            size: s.fontSize,
            lineHeight: s.lineHeight,
            whiteSpace: s.whiteSpace,
            weight: s.fontWeight,
          });
        }
        return {
          compatMode: document.compatMode,
          regions,
          texts,
          stylesheets: Array.from(
            document.querySelectorAll("link[rel=stylesheet]"),
          ).map((e) => e.getAttribute("href")),
        };
      })),
    });
  }
  return { parent, documents };
}
function documentPixels(
  image: Buffer,
  c: Case,
  geometry: Awaited<ReturnType<typeof measure>>,
) {
  const source = PNG.sync.read(image);
  const parent = geometry.parent as unknown as Record<
    string,
    { rect: number[] }
  >;
  let region: number[];
  if (c.job.slide_layout) region = parent.slideStage.rect;
  else {
    const origin = parent.original.rect;
    const viewer = geometry.documents.find((document) =>
      document.url.endsWith(
        c.job.filename.endsWith(".xlsx") ? "/workbook" : "/pdf",
      ),
    )!;
    const stage = viewer.regions.find(
      (r) =>
        r.tag ===
        (c.job.filename.endsWith(".xlsx") ? "sheetStage" : "pdfStage"),
    )!.rect;
    region = [origin[0] + stage[0], origin[1] + stage[1], stage[2], stage[3]];
  }
  const [x, y, width, height] = region.map(Math.round);
  expect(
    x >= 0 &&
      y >= 0 &&
      x + width <= source.width &&
      y + height <= source.height,
  ).toBe(true);
  const clipped = new PNG({ width, height });
  for (let row = 0; row < height; row++)
    source.data.copy(
      clipped.data,
      row * width * 4,
      ((y + row) * source.width + x) * 4,
      ((y + row) * source.width + x + width) * 4,
    );
  return { image: PNG.sync.write(clipped), region };
}
async function compareImages(a: Buffer, b: Buffer, path: string) {
  const old = PNG.sync.read(a),
    next = PNG.sync.read(b);
  expect([next.width, next.height]).toEqual([old.width, old.height]);
  const diff = new PNG({ width: old.width, height: old.height });
  const changed = pixelmatch(
    old.data,
    next.data,
    diff.data,
    old.width,
    old.height,
    { threshold: 0, includeAA: true },
  );
  await writeFile(new URL(path, out), PNG.sync.write(diff));
  return changed;
}
function maximumChannelDifference(a: Buffer, b: Buffer) {
  const x = PNG.sync.read(a),
    y = PNG.sync.read(b);
  let maximum = 0;
  for (let i = 0; i < x.data.length; i++)
    maximum = Math.max(maximum, Math.abs(x.data[i] - y.data[i]));
  return maximum;
}
function unexplainedPixels(
  before: Buffer,
  after: Buffer,
  repeated: Buffer[],
  colorNoise: number,
) {
  const a = PNG.sync.read(before),
    b = PNG.sync.read(after),
    repeats = repeated.map((sample) => PNG.sync.read(sample));
  let unexplained = 0;
  for (let i = 0; i < a.data.length; i += 4) {
    let differs = false;
    for (let channel = 0; channel < 4; channel++) {
      const delta = Math.abs(a.data[i + channel] - b.data[i + channel]);
      const observed = Math.max(
        ...repeats.map((r) =>
          Math.abs(a.data[i + channel] - r.data[i + channel]),
        ),
      );
      if (delta > Math.max(colorNoise, observed)) differs = true;
    }
    if (differs) unexplained++;
  }
  return unexplained;
}
async function assetHashes(page: Page, c: Case) {
  const files = new Set<string>();
  for (const source of await page.evaluate(() => {
    const rail = document.getElementById("slideThumbnails"),
      bounds = rail?.getBoundingClientRect();
    return bounds?.width
      ? Array.from(rail!.querySelectorAll("img"))
          .filter((img) => {
            const r = img.getBoundingClientRect();
            return r.bottom > bounds.top && r.top < bounds.bottom;
          })
          .map((img) => img.src)
      : [];
  }))
    if (source) files.add(source);
  for (const f of page.frames().slice(1)) {
    if (
      f.isDetached() ||
      f.url() === "about:blank" ||
      !(await (await f.frameElement()).isVisible())
    )
      continue;
    if (f.url() !== "about:blank") files.add(f.url());
    for (const u of await f.evaluate(() =>
      Array.from(document.querySelectorAll("link[href],img[src]")).map((e) =>
        e instanceof HTMLLinkElement ? e.href : (e as HTMLImageElement).src,
      ),
    ))
      if (u.startsWith("http")) files.add(u);
  }
  const result: Record<string, string> = {};
  for (const url of files) {
    const r = await page.request.get(url);
    expect(r.ok(), `${c.name}: ${url}`).toBeTruthy();
    result[new URL(url).pathname + new URL(url).search] = createHash("sha256")
      .update(await r.body())
      .digest("hex");
  }
  return result;
}
test("all saved units, translations, breakpoints, zoom and panels preserve document layout", async ({
  page,
  request,
  browserName,
}) => {
  await mkdir(out, { recursive: true });
  const library = (await (await request.get("/api/library")).json()) as {
    jobs: Job[];
  };
  const cases: Case[] = [];
  for (const job of library.jobs) {
    if (!job.preview) continue;
    for (let number = 1; number <= job.pages; number++)
      cases.push({
        job,
        number,
        language: "original",
        name: `${job.id}-${number}-original-1440`,
        width: 1440,
        height: 900,
      });
    const overview = (await (
      await request.get(`/api/jobs/${job.id}/translations`)
    ).json()) as {
      units: {
        number: number;
        languages: Record<string, { available: boolean }>;
      }[];
    };
    for (const u of overview.units)
      for (const [language, r] of Object.entries(u.languages))
        if (r.available)
          cases.push({
            job,
            number: u.number,
            language,
            name: `${job.id}-${u.number}-${language}-1440`,
            width: 1440,
            height: 900,
          });
  }
  // Detailed cases straddle toolbar, rail and native iframe breakpoints.
  for (const job of library.jobs.filter(
    (j) => j.pages === 34 || j.id === "8".repeat(32) || j.id === "9".repeat(32),
  )) {
    for (const width of [
      379, 381, 390, 579, 581, 599, 601, 619, 621, 799, 801, 849, 851, 1099,
      1101,
    ])
      cases.push({
        job,
        number: job.pages === 34 ? 13 : 1,
        language: "original",
        name: `${job.id}-detail-${width}`,
        width,
        height: width === 390 ? 844 : 900,
      });
    for (const zoom of [1, job.filename.endsWith(".xlsx") ? 1.953125 : 2])
      cases.push({
        job,
        number: job.pages === 34 ? 5 : 1,
        language: "original",
        zoom,
        rail: false,
        name: `${job.id}-zoom-${zoom}`,
        width: 1440,
        height: 900,
      });
  }
  const ppt = library.jobs.find((j) => j.pages === 34);
  if (ppt)
    cases.push({
      job: ppt,
      number: 15,
      language: "original",
      name: "ppt-slide-15-detail",
      width: 800,
      height: 900,
    });
  for (const job of library.jobs.filter(
    (j) =>
      j.id === "7".repeat(32) ||
      j.id === "8".repeat(32) ||
      j.id === "9".repeat(32),
  ))
    cases.push({
      job,
      number: 1,
      language: "original",
      panel: true,
      name: `${job.id}-explanation`,
      width: 1440,
      height: 900,
    });
  await writeFile(
    new URL("cases.json", out),
    JSON.stringify(
      {
        browser: browserName,
        version: page.context().browser()?.version(),
        devicePixelRatio: await page.evaluate(() => devicePixelRatio),
        cases,
      },
      null,
      2,
    ),
  );
  // Calibrate only from repeated legacy captures; never from the migrated UI.
  const calibrationCase = cases.find(
    (c) => c.name === "77777777777777777777777777777777-explanation",
  );
  let legacyColorNoise = 0;
  if (calibrationCase) {
    await prepare(page, baselinePath, calibrationCase);
    const first = await capture(page),
      geometry = await measure(page);
    await writeFile(new URL("legacy-color-calibration-before.png", out), first);
    for (let attempt = 0; attempt < 5 && !legacyColorNoise; attempt++) {
      await prepare(page, baselinePath, calibrationCase);
      expect(await measure(page)).toEqual(geometry);
      const repeat = await capture(page);
      legacyColorNoise = maximumChannelDifference(first, repeat);
      expect(
        legacyColorNoise,
        "legacy raster color noise is at most one level per channel",
      ).toBeLessThanOrEqual(1);
      await writeFile(
        new URL(`legacy-color-calibration-repeat-${attempt}.png`, out),
        repeat,
      );
    }
    await writeFile(
      new URL("legacy-color-calibration.json", out),
      JSON.stringify(
        {
          case: calibrationCase.name,
          browserVersion: page.context().browser()?.version(),
          maximumChannelDifference: legacyColorNoise,
          geometry,
        },
        null,
        2,
      ),
    );
  }
  const results: unknown[] = [];
  const caseErrors: string[] = [];
  await writeFile(new URL("case-errors.json", out), "[]");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const requestedCases: string[] | undefined = process.env.UI_CASES
    ? JSON.parse(process.env.UI_CASES)
    : undefined;
  for (const c of cases.filter(
    (c) =>
      (!process.env.UI_CASE || c.name.includes(process.env.UI_CASE)) &&
      (!requestedCases || requestedCases.includes(c.name)),
  )) {
    try {
      console.log(`Layout ${c.name}`);
      await prepare(page, baselinePath, c);
      const baseline = await measure(page),
        before = await capture(page),
        assets = await assetHashes(page, c);
      const documentBefore = documentPixels(before, c, baseline);
      await writeFile(
        new URL(`${c.name}-document-before.png`, out),
        documentBefore.image,
      );
      const oldSvg = await nativeSvg(page);
      await writeFile(new URL(`${c.name}-before.png`, out), before);
      // Repeated legacy capture establishes whether exact pixels are reproducible.
      await prepare(page, baselinePath, c);
      const repeated = await capture(page);
      const documentRepeated = documentPixels(repeated, c, baseline);
      await writeFile(
        new URL(`${c.name}-document-repeat.png`, out),
        documentRepeated.image,
      );
      await writeFile(new URL(`${c.name}-repeat.png`, out), repeated);
      const noise = await compareImages(
        documentBefore.image,
        documentRepeated.image,
        `${c.name}-document-repeat-diff.png`,
      );
      const repeatDelta = maximumChannelDifference(
        documentBefore.image,
        documentRepeated.image,
      );
      expect(
        await measure(page),
        `legacy repeated geometry: ${c.name}`,
      ).toEqual(baseline);
      if (process.env.UI_BASELINE) {
        results.push({
          name: c.name,
          baseline,
          repeatedPixels: noise,
          assets,
          nativeSvg: oldSvg,
        });
        continue;
      }
      await prepare(page, "/ui/", c);
      const after = await measure(page),
        image = await capture(page),
        afterAssets = await assetHashes(page, c);
      const documentAfter = documentPixels(image, c, after);
      expect(documentAfter.region).toEqual(documentBefore.region);
      await writeFile(
        new URL(`${c.name}-document-after.png`, out),
        documentAfter.image,
      );
      const newSvg = await nativeSvg(page);
      expect(newSvg, `native SVG geometry and markup: ${c.name}`).toEqual(
        oldSvg,
      );
      await writeFile(new URL(`${c.name}-after.png`, out), image);
      await writeFile(
        new URL(`${c.name}-post-capture.json`, out),
        JSON.stringify(await measure(page), null, 2),
      );
      const fullChanged = await compareImages(
        before,
        image,
        `${c.name}-diff.png`,
      );
      const changed = await compareImages(
        documentBefore.image,
        documentAfter.image,
        `${c.name}-document-diff.png`,
      );
      const legacySamples = [documentRepeated.image];
      let unexplained = unexplainedPixels(
        documentBefore.image,
        documentAfter.image,
        legacySamples,
        legacyColorNoise,
      );
      // Audit a mismatch with additional old-UI captures. Only differences
      // actually reproduced at the same pixel/channel in the old UI count.
      // No mask, geometry tolerance or migrated-image-derived threshold.
      for (let attempt = 0; unexplained > 0 && attempt < 8; attempt++) {
        await prepare(page, baselinePath, c);
        expect(await measure(page), `legacy audit geometry: ${c.name}`).toEqual(
          baseline,
        );
        const sample = await capture(page);
        const documentSample = documentPixels(sample, c, baseline);
        legacySamples.push(documentSample.image);
        await writeFile(
          new URL(`${c.name}-legacy-audit-${attempt}.png`, out),
          sample,
        );
        unexplained = unexplainedPixels(
          documentBefore.image,
          documentAfter.image,
          legacySamples,
          legacyColorNoise,
        );
      }
      await writeFile(
        new URL(`${c.name}.json`, out),
        JSON.stringify(
          {
            baseline,
            after,
            assets,
            afterAssets,
            changedPixels: changed,
            unexplainedPixels: unexplained,
            repeatedMaximumChannelDifference: repeatDelta,
            repeatedPixels: noise,
            maximumChannelDifference: maximumChannelDifference(
              documentBefore.image,
              documentAfter.image,
            ),
            fullPageChangedPixels: fullChanged,
            documentRegion: documentBefore.region,
            legacyColorNoise,
            legacyCaptureCount: legacySamples.length + 1,
            nativeSvg: oldSvg,
          },
          null,
          2,
        ),
      );
      results.push({
        name: c.name,
        changedPixels: changed,
        unexplainedPixels: unexplained,
        maximumChannelDifference: maximumChannelDifference(
          documentBefore.image,
          documentAfter.image,
        ),
        fullPageChangedPixels: fullChanged,
        repeatedPixels: noise,
      });
      await writeFile(
        new URL("results.json", out),
        JSON.stringify({ results, errors }, null, 2),
      );
      expect(afterAssets, `assets: ${c.name}`).toEqual(assets);
      expect(after, `dimensions and text lines: ${c.name}`).toEqual(baseline);
      expect(
        unexplained,
        `pixels: ${c.name}; allowances come only from repeated legacy captures`,
      ).toBe(0);
    } catch (error) {
      caseErrors.push(`${c.name}: ${String(error)}`);
      await writeFile(
        new URL("case-errors.json", out),
        JSON.stringify(caseErrors, null, 2),
      );
    }
  }
  expect(caseErrors).toEqual([]);
  expect(errors).toEqual([]);
  await writeFile(
    new URL("results.json", out),
    JSON.stringify({ results, errors }, null, 2),
  );
});

test("Excel natural size remains pixel-identical to the original frame bridge", async ({
  page,
  request,
}) => {
  await mkdir(out, { recursive: true });
  const oldBridge = await readFile(
    new URL("native-fit-before/sheet-frame.js", out),
    "utf8",
  );
  const { jobs } = (await (await request.get("/api/library")).json()) as {
    jobs: Job[];
  };
  for (const job of jobs.filter((j) => j.filename.endsWith(".xlsx"))) {
    for (let number = 1; number <= job.pages; number++) {
      const c: Case = {
        job,
        number,
        language: "original",
        name: `${job.id}-${number}-natural-bridge`,
        width: 1440,
        height: 900,
        zoom: 1,
      };
      await page.route("**/static/sheet-frame.js", (route) =>
        route.fulfill({
          contentType: "application/javascript",
          body: oldBridge,
        }),
      );
      await prepare(page, baselinePath, c);
      const before = await capture(page),
        dimensions = await measure(page);
      await page.unroute("**/static/sheet-frame.js");
      await prepare(page, baselinePath, c);
      const after = await capture(page);
      expect(await measure(page)).toEqual(dimensions);
      await writeFile(new URL(`${c.name}-before.png`, out), before);
      await writeFile(new URL(`${c.name}-after.png`, out), after);
      expect(await compareImages(before, after, `${c.name}-diff.png`)).toBe(0);
    }
  }
});

test("shared library and document workbench preserve responsive layout", async ({
  page,
  request,
}) => {
  await mkdir(out, { recursive: true });
  const saved = await (await request.get("/api/library")).json();
  const calibration = JSON.parse(
    await readFile(new URL("legacy-color-calibration.json", out), "utf8"),
  ) as { maximumChannelDifference: number };
  const records: unknown[] = [];
  for (const width of [390, 800, 1440]) {
    for (const view of ["library", "tables", "structure", "rag"]) {
      const name = `shared-${view}-${width}`;
      const images: Buffer[] = [],
        geometries: unknown[] = [];
      for (const path of [baselinePath, "/ui/"]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(
          view === "library" ? path : `${path}?job=${"8".repeat(32)}`,
        );
        if (view === "library")
          await expect(page.locator("#jobs tr")).toHaveCount(saved.jobs.length);
        else {
          await expect(page.locator("#filename")).toHaveText("QA.xlsx");
          await page.locator(`#tab-${view}`).click();
          if (view === "tables")
            await expect(
              page.locator("#tableGrid .ag-cell").first(),
            ).toBeVisible();
          if (view === "structure")
            await expect(
              page.locator("#structure article").first(),
            ).toBeVisible();
          if (view === "rag")
            await expect(
              page.locator("#ragCards article").first(),
            ).toBeVisible();
        }
        await ready(page);
        await page.locator(view === "library" ? "h1" : "#filename").click();
        await page.waitForTimeout(300);
        await page.evaluate(() => {
          document.documentElement.style.visibility = "hidden";
        });
        await page.waitForTimeout(50);
        await page.evaluate(() => {
          document.documentElement.style.removeProperty("visibility");
        });
        await ready(page);
        await page.waitForTimeout(100);
        await capture(page);
        await ready(page);
        const geometry = await page.evaluate(() => {
          const selectors = [
            ".library-bar",
            ".library-content",
            ".file-list",
            "#viewerToolbar",
            "#documentPanels",
            "#tableGrid",
            ".table-card",
            ".table-toolbar",
            ".table-grid",
            ".ag-header",
            ".ag-row",
            "#structure article",
            "#ragCards article",
          ];
          return Object.fromEntries(
            selectors.map((selector) => [
              selector,
              Array.from(document.querySelectorAll(selector))
                .filter((e) => e.getBoundingClientRect().width)
                .map((e) => {
                  const r = e.getBoundingClientRect();
                  return [r.x, r.y, r.width, r.height].map((n) =>
                    Number(n.toFixed(3)),
                  );
                }),
            ]),
          );
        });
        geometries.push(geometry);
        const image = await capture(page);
        images.push(image);
        await writeFile(
          new URL(
            `${name}-${path === baselinePath ? "before" : "after"}.png`,
            out,
          ),
          image,
        );
      }
      const changed = await compareImages(
        images[0],
        images[1],
        `${name}-diff.png`,
      );
      const delta = maximumChannelDifference(images[0], images[1]);
      records.push({
        name,
        changedPixels: changed,
        maximumChannelDifference: delta,
        before: geometries[0],
        after: geometries[1],
      });
      await writeFile(
        new URL("shared-results.json", out),
        JSON.stringify(records, null, 2),
      );
      expect(geometries[1], name).toEqual(geometries[0]);
      expect(delta, name).toBeLessThanOrEqual(
        calibration.maximumChannelDifference,
      );
    }
  }
});
