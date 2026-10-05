/** Independent opaque frames and removal of an already displayed document. */
import {
  chromium,
  expect,
} from "../../frontend/node_modules/@playwright/test/index.mjs";
import { readFile, writeFile, rename } from "node:fs/promises";
const path = ".cache/viewer-embedding/config.json";
const raw = await readFile(path, "utf8");
const config = JSON.parse(raw);
const refs = JSON.parse(
  await readFile(".cache/viewer-embedding/refs.json", "utf8"),
);
const user = Object.keys(config.users)[0];
const tool = async (name, args) => {
  const r = await fetch(config.public_url + "/host/tools/" + name, {
    method: "POST",
    headers: {
      authorization: "Bearer " + config.connector_token,
      "content-type": "application/json",
    },
    body: JSON.stringify({ user_id: user, arguments: args }),
  });
  expect(r.status).toBe(200);
  return r.json();
};
const save = async (value) => {
  await writeFile(path + ".tmp", value, { mode: 0o600 });
  await rename(path + ".tmp", path);
};
const browser = await chromium.launch({
  ...(process.env.UI_BROWSER_EXECUTABLE
    ? { executablePath: process.env.UI_BROWSER_EXECUTABLE }
    : { channel: "chrome" }),
  headless: true,
});
try {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
  });
  await page.setContent(
    '<div style="display:flex"><iframe id="one" sandbox="allow-scripts allow-downloads" style="width:50%;height:760px"></iframe><iframe id="two" sandbox="allow-scripts allow-downloads" style="width:50%;height:760px"></iframe></div>',
  );
  for (const [index, id] of ["one", "two"].entries()) {
    const view = await tool("request_document_view", {
      ...refs.pdf,
      location: { kind: "page", number: index + 1 },
    });
    await page
      .locator("#" + id)
      .evaluate((el, url) => (el.src = url), view.embed_url);
    const frame = page.frameLocator("#" + id);
    await expect(frame.getByLabel("接続コード")).toBeVisible();
    await tool("connect_document_view", {
      code: await frame.getByLabel("接続コード").textContent(),
    });
    await expect(frame.locator("#detail")).toBeVisible();
    await expect(
      frame.frameLocator("#original").locator("#pdfPage"),
    ).toHaveValue(String(index + 1));
  }
  const one = page.frameLocator("#one");
  const two = page.frameLocator("#two");
  await one.frameLocator("#original").locator("#pdfZoom").selectOption("150");
  await one.frameLocator("#original").locator("#pdfNext").click();
  await one.frameLocator("#original").locator("#pdfPrevious").click();
  await expect(one.frameLocator("#original").locator("#pdfPage")).toHaveValue(
    "1",
  );
  await expect(two.frameLocator("#original").locator("#pdfPage")).toHaveValue(
    "2",
  );
  await expect(
    two.frameLocator("#original").locator("#pdfZoom option[value=current]"),
  ).not.toHaveText("150%");
  const jobId = await one
    .frameLocator("#original")
    .locator("body")
    .getAttribute("data-job");
  await page.locator("#one").evaluate(
    (el, jobId) =>
      el.contentWindow.postMessage(
        {
          type: "docling-text-selection",
          jobId,
          unitId: "page-1",
          text: "spoof",
        },
        "*",
      ),
    jobId,
  );
  await expect(one.locator("#previewCopy")).toBeDisabled();
  config.users[user].scopes = [{ collection_id: "other" }];
  await save(JSON.stringify(config));
  await expect(one.locator("#detail")).toHaveCount(0, { timeout: 20000 });
  await expect(two.locator("#detail")).toHaveCount(0, { timeout: 20000 });
  await expect(one.getByRole("button", { name: "資料へ再接続" })).toBeVisible();
  await writeFile(
    "qa/viewer-embedding/instances-results.json",
    JSON.stringify(
      {
        same_document_independent_pages: true,
        independent_zoom: true,
        matching_reference_wrong_window_ignored: true,
        displayed_content_cleared_after_revocation: true,
        heartbeat_seconds: 15,
      },
      null,
      2,
    ) + "\n",
  );
  console.log("Independent frames and live policy revocation passed");
} finally {
  await save(raw);
  await browser.close();
}
