// Check the final build through the real HTTPS/JWT app using saved page assets.
import {
  chromium,
  expect,
} from "../../frontend/node_modules/@playwright/test/index.mjs";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const output = path.resolve(
  process.env.DOCLING_RECOVERY_QA_OUTPUT ||
    "qa/performance/recovery-viewer-20261007",
);
const base = process.env.DOCLING_QA_URL || "https://127.0.0.1:8898";
const id = "d".repeat(32);
const { token } = JSON.parse(
  await readFile(path.join(output, "credentials.json")),
);
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const anonymous = await browser.newContext({ ignoreHTTPSErrors: true });
  for (const query of ["inline_fonts=true", "thumbnail=true"]) {
    const refused = await anonymous.request.get(
      `${base}/files/${id}/progressive-preview/page-1.html?${query}`,
    );
    expect(refused.status()).toBe(401);
  }
  await anonymous.close();
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 900 },
  });
  await context.addCookies([
    {
      name: "mplm_access_token",
      value: token,
      url: base,
      secure: true,
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  const page = await context.newPage();
  const requests = [],
    failures = [],
    issues = [];
  let inFlight = 0,
    maximumInFlight = 0;
  const thumbs = (request) =>
    new URL(request.url()).searchParams.has("thumbnail");
  page.on("request", (request) => {
    requests.push(request.url());
    if (thumbs(request))
      maximumInFlight = Math.max(maximumInFlight, ++inFlight);
  });
  page.on("requestfinished", (request) => {
    if (thumbs(request)) inFlight--;
  });
  page.on("requestfailed", (request) => {
    if (thumbs(request)) inFlight--;
    failures.push({ url: request.url(), error: request.failure()?.errorText });
  });
  page.on("response", (response) => {
    if (response.status() >= 400)
      failures.push({ url: response.url(), status: response.status() });
  });
  page.on("pageerror", (error) => issues.push(error.message));
  await page.goto(`${base}/?job=${id}`);
  await expect(page.locator("#slidePicker option")).toHaveCount(530);
  await expect(page.locator("#translateOpen")).toBeDisabled();
  await expect(page.locator("#translationLanguage")).toBeDisabled();
  expect(
    await page
      .locator("#explanationDisclosure,.lightweight-preview-notice")
      .count(),
  ).toBe(0);

  const rendered = [];
  async function checkPage(number) {
    const iframe = page.locator("#slideCanvas iframe");
    await expect(iframe).toHaveAttribute(
      "src",
      new RegExp(`page-${number}\\.html\\?inline_fonts=true$`),
    );
    await expect(iframe).toHaveAttribute("sandbox", "");
    expect(await page.locator("#slideCanvas iframe").count()).toBe(1);
    const frame = await (await iframe.elementHandle()).contentFrame();
    await frame.waitForSelector("svg");
    const metrics = await frame.evaluate(async () => {
      await document.fonts.ready;
      const svg = document.querySelector("svg");
      const text = svg.querySelector("text");
      return {
        layout: JSON.parse(svg.dataset.textLayout),
        scroll: [
          document.documentElement.scrollWidth,
          document.documentElement.scrollHeight,
        ],
        client: [
          document.documentElement.clientWidth,
          document.documentElement.clientHeight,
        ],
        fontCount: document.fonts.size,
        loadedFonts: [...document.fonts].filter(
          (font) => font.status === "loaded",
        ).length,
        textWidth: text?.getBoundingClientRect().width || 0,
        cssLinks: document.querySelectorAll('link[rel="stylesheet"]').length,
      };
    });
    expect(metrics.layout.number).toBe(number);
    expect(metrics.scroll[0]).toBeLessThanOrEqual(metrics.client[0]);
    expect(metrics.scroll[1]).toBeLessThanOrEqual(metrics.client[1]);
    expect(metrics.cssLinks).toBe(0);
    expect(metrics.textWidth).toBeGreaterThan(0);
    if (metrics.fontCount) expect(metrics.loadedFonts).toBeGreaterThan(0);
    const response = await context.request.get(
      `${base}${await iframe.getAttribute("src")}`,
    );
    expect(response.status()).toBe(200);
    const csp = response.headers()["content-security-policy"];
    expect(csp).toContain("sandbox");
    expect(csp).not.toContain("allow-same-origin");
    expect(csp).not.toContain("allow-scripts");
    rendered.push({
      number,
      ...metrics,
      layout: { width: metrics.layout.width, height: metrics.layout.height },
    });
  }
  const loadedImages = page.locator("#slideThumbnails img[src]");
  const waitImages = async () => {
    await expect
      .poll(() =>
        loadedImages.evaluateAll((images) =>
          images.every((image) => image.complete && image.naturalWidth > 0),
        ),
      )
      .toBe(true);
    await expect.poll(() => inFlight).toBe(0);
  };
  await checkPage(1);
  await waitImages();
  const initialThumbs = requests.filter((url) =>
    new URL(url).searchParams.has("thumbnail"),
  ).length;
  expect(initialThumbs).toBeGreaterThan(0);
  expect(initialThumbs).toBeLessThan(30);
  expect(
    requests.filter((url) => new URL(url).searchParams.has("inline_fonts")),
  ).toHaveLength(1);
  await page.getByRole("button", { name: "スライド 2", exact: true }).click();
  await checkPage(2);
  await page.getByRole("button", { name: "前のスライド", exact: true }).click();
  await checkPage(1);
  await page.getByRole("button", { name: "次のスライド", exact: true }).click();
  await checkPage(2);
  await page.getByRole("button", { name: "拡大", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "画面に合わせる", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await page
    .getByRole("button", { name: "画面に合わせる", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "画面に合わせる", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page
    .getByRole("button", { name: "サムネイルを表示・非表示", exact: true })
    .click();
  await expect(page.locator("#slideThumbnails")).toBeHidden();
  await page
    .getByRole("button", { name: "サムネイルを表示・非表示", exact: true })
    .click();
  await expect(page.locator("#slideThumbnails")).toBeVisible();
  await page.locator("#slidePicker").selectOption("530");
  await checkPage(530);
  await waitImages();
  const finalThumbs = requests.filter((url) =>
    new URL(url).searchParams.has("thumbnail"),
  ).length;
  expect(finalThumbs).toBeLessThan(60);
  expect(maximumInFlight).toBeLessThanOrEqual(2);
  await page.screenshot({ path: path.join(output, "viewer-page-530.png") });
  await page.locator("#saveMenu summary").click();
  await page.locator("#printPreviewOpen").click();
  await expect(
    page.getByRole("combobox", { name: "印刷範囲", exact: true }),
  ).toHaveValue("current");
  await expect(
    page.getByRole("button", { name: "印刷・PDF保存", exact: true }),
  ).toBeEnabled({ timeout: 45000 });
  const printFrame = await (
    await page.locator("#printPreviewFrame").elementHandle()
  ).contentFrame();
  const printMetrics = await printFrame.evaluate(async () => {
    await document.fonts.ready;
    return [...document.querySelectorAll(".print-unit")].map((unit) => ({
      number: Number(unit.dataset.number),
      svg: !!unit.firstElementChild.shadowRoot.querySelector("svg"),
      width: unit.getBoundingClientRect().width,
    }));
  });
  expect(printMetrics).toHaveLength(1);
  expect(printMetrics[0].number).toBe(530);
  expect(printMetrics[0].svg).toBe(true);
  await printFrame.evaluate(() => {
    window.print = () => {
      document.documentElement.dataset.printInvoked = "true";
    };
  });
  await page
    .getByRole("button", { name: "印刷・PDF保存", exact: true })
    .click();
  expect(
    await printFrame.evaluate(
      () => document.documentElement.dataset.printInvoked,
    ),
  ).toBe("true");
  await page.screenshot({ path: path.join(output, "print-page-530.png") });
  const beforeDeepLink = requests.length;
  await page.goto(`${base}/?job=${id}&unit=530`);
  await checkPage(530);
  await waitImages();
  const railBox = await page.locator("#slideThumbnails").boundingBox();
  const selectedBox = await page
    .getByRole("button", { name: "スライド 530", exact: true })
    .boundingBox();
  expect(selectedBox.y).toBeGreaterThanOrEqual(railBox.y);
  expect(selectedBox.y + selectedBox.height).toBeLessThanOrEqual(
    railBox.y + railBox.height,
  );
  const deepLinkThumbs = requests
    .slice(beforeDeepLink)
    .filter((url) => new URL(url).searchParams.has("thumbnail"));
  expect(deepLinkThumbs.length).toBeGreaterThan(0);
  expect(
    deepLinkThumbs.every(
      (url) => Number(/page-(\d+)\.html/.exec(url)[1]) >= 500,
    ),
  ).toBe(true);

  // Delay the first two thumbnails, then jump before the queue can drain.
  const fast = await context.newPage();
  const fastThumbs = [];
  let releaseThumbnails;
  const delayed = new Promise((resolve) => {
    releaseThumbnails = resolve;
  });
  fast.on("pageerror", (error) => issues.push(error.message));
  fast.on("response", (response) => {
    if (response.status() >= 400)
      failures.push({ url: response.url(), status: response.status() });
  });
  await fast.route("**/files/**?thumbnail=true", async (route) => {
    fastThumbs.push(route.request().url());
    await delayed;
    await route.continue();
  });
  await fast.goto(`${base}/?job=${id}`);
  await expect(fast.locator("#slidePicker option")).toHaveCount(530);
  await expect.poll(() => fastThumbs.length).toBe(2);
  await fast.locator("#slidePicker").selectOption("530");
  await expect
    .poll(() =>
      fast.locator("#slideThumbnails").evaluate((rail) => rail.scrollTop),
    )
    .toBeGreaterThan(0);
  await fast.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  releaseThumbnails();
  await expect.poll(() => fastThumbs.length).toBeGreaterThan(2);
  await expect
    .poll(() =>
      fast.locator("#slideThumbnails").evaluate((rail) => {
        const bounds = rail.getBoundingClientRect();
        return [...rail.querySelectorAll("img")].every((image) => {
          const box = image.getBoundingClientRect();
          if (
            box.bottom < Math.max(0, bounds.top - 120) ||
            box.top > Math.min(innerHeight, bounds.bottom + 120)
          )
            return true;
          return (
            image.hasAttribute("src") &&
            image.complete &&
            image.naturalWidth > 0
          );
        });
      }),
    )
    .toBe(true);
  await expect
    .poll(() =>
      fast
        .locator("#slideThumbnails img[src]")
        .evaluateAll((images) =>
          images.every((image) => image.complete && image.naturalWidth > 0),
        ),
    )
    .toBe(true);
  expect(
    fastThumbs
      .slice(2)
      .every((url) => Number(/page-(\d+)\.html/.exec(url)[1]) >= 500),
  ).toBe(true);
  await fast.close();
  expect(
    requests.filter((url) =>
      /\/translations|\/view\/|\/api\/jobs\/.*\/thumbnail|revision-530\.html/.test(
        url,
      ),
    ),
  ).toEqual([]);
  expect(failures).toEqual([]);
  expect(issues).toEqual([]);
  const report = {
    rendered,
    initialThumbs,
    finalThumbs,
    maximumInFlight,
    printMetrics,
    failures,
    issues,
    requests,
    deepLinkThumbs,
    fastThumbs,
  };
  await writeFile(
    path.join(output, "browser-report.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(
    JSON.stringify({
      renderedPages: rendered.map((p) => p.number),
      initialThumbs,
      finalThumbs,
      maximumInFlight,
      printMetrics,
      deepLinkThumbnails: deepLinkThumbs.length,
      fastJumpThumbnails: fastThumbs.length,
      failures,
      issues,
    }),
  );
} finally {
  await browser.close();
}
