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
const browser = await chromium.launch({
  ...(process.env.UI_BROWSER_EXECUTABLE
    ? { executablePath: process.env.UI_BROWSER_EXECUTABLE }
    : { channel: "chrome" }),
  headless: true,
});
const page = await browser.newPage();
try {
  const result = await tool("request_document_view", {
    ...refs.txt,
    location: { kind: "document", number: 1 },
  });
  await page.setContent(
    '<iframe id="embed" sandbox="allow-scripts allow-downloads" style="width:100%;height:780px"></iframe><script>window.prompts=[];addEventListener("message",e=>{if(e.source===document.getElementById("embed").contentWindow&&e.data?.type==="input:prompt")prompts.push(e.data.text)});</script>',
  );
  await page
    .locator("#embed")
    .evaluate((el, url) => (el.src = url), result.embed_url);
  const viewer = page.frameLocator("#embed");
  const code = viewer.getByLabel("接続コード");
  await expect(code).toBeVisible();
  await tool("connect_document_view", { code: await code.textContent() });
  await expect(viewer.locator("#detail")).toBeVisible();
  await expect(viewer.locator("#previewCopy")).toBeDisabled();
  // A matching-shaped message from the host window is not a document selection.
  await page.evaluate(() =>
    document.getElementById("embed").contentWindow.postMessage(
      {
        type: "docling-text-selection",
        jobId: "spoof",
        unitId: "document-1",
        text: "spoof",
      },
      "*",
    ),
  );
  await expect(viewer.locator("#previewCopy")).toBeDisabled();
  const documentFrame = viewer.frameLocator("#original");
  await expect(documentFrame.locator("body")).toContainText(
    "Synthetic inventory evidence",
  );
  await documentFrame.locator("body").evaluate((body) => {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.textContent.includes("Synthetic inventory evidence")) {
        const range = document.createRange();
        range.selectNodeContents(node);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        document.dispatchEvent(new Event("selectionchange"));
        break;
      }
    }
  });
  await expect(viewer.locator("#previewCopy")).toBeEnabled();
  await viewer.getByRole("button", { name: "この箇所について質問" }).click();
  const prompt = await page.evaluate(() => window.prompts.at(-1));
  expect(prompt).toContain("Synthetic inventory evidence");
  expect(prompt).toContain(refs.txt.source_revision);
  expect(prompt).not.toContain("/viewer/session/");
  await viewer.locator("#previewCopy").click();
  await expect(viewer.locator("#previewCopyFallback")).toBeVisible();
  await expect(viewer.getByLabel("コピーする文字")).toContainText(
    "Synthetic inventory evidence",
  );
  await writeFile(
    "qa/viewer-embedding/selection-results.json",
    JSON.stringify(
      {
        real_selection: true,
        reference_in_prompt: true,
        wrong_window_ignored: true,
        clipboard_fallback: true,
        session_credential_in_prompt: false,
      },
      null,
      2,
    ) + "\n",
  );
  console.log("Selection, prompt and clipboard fallback passed");
} finally {
  await browser.close();
}
