import {
  chromium,
  expect,
} from "../../frontend/node_modules/@playwright/test/index.mjs";
import { readFile, writeFile } from "node:fs/promises";
const accounts = JSON.parse(
  await readFile(".cache/viewer-embedding/accounts.json", "utf8"),
);
const browser = await chromium.launch({
  ...(process.env.UI_BROWSER_EXECUTABLE
    ? { executablePath: process.env.UI_BROWSER_EXECUTABLE }
    : { channel: "chrome" }),
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.on("console", (m) => {
  if (m.type() === "error") console.log("Browser:", m.text().slice(0, 240));
});
await page.goto("http://127.0.0.1:18880");
await page.evaluate((token) => {
  localStorage.setItem("token", token);
  localStorage.setItem(
    "settings",
    JSON.stringify({ functionCall: "native", notificationEnabled: false }),
  );
}, accounts[0].token);
await page.goto("http://127.0.0.1:18880/?tools=docling_viewer");
await page.waitForTimeout(2500);
if (
  await page
    .getByRole("button", { name: "Okay, Let's Go!" })
    .isVisible()
    .catch(() => false)
)
  await page.getByRole("button", { name: "Okay, Let's Go!" }).click();
await page.locator("#chat-input").fill("合成資料を検索してください");
await page.locator("#chat-input").press("Enter");
await page.waitForTimeout(10000);
await page.getByText("Synthetic xlsx", { exact: true }).first().click();
await page.waitForTimeout(1500);
const viewer = page
  .frames()
  .find((f) => f.url().startsWith("http://127.0.0.1:18868/viewer?"));
if (!viewer) throw new Error("Citation did not open viewer");
await expect(viewer.getByLabel("接続コード")).toBeVisible();
await viewer
  .getByRole("button", { name: "接続コードをチャット入力へ" })
  .click();
await expect(page.locator("#chat-input")).toContainText("接続コード");
await page.locator("#chat-input").press("Enter");
await expect(viewer.locator("#detail")).toBeVisible({ timeout: 30000 });
await expect(
  viewer.frameLocator("#original").locator("#sheetTabs [aria-selected=true]"),
).toBeVisible();
await viewer.getByRole("button", { name: "この箇所について質問" }).click();
await expect(page.locator("#chat-input")).toContainText("資料参照:");
await page.screenshot({ path: "qa/viewer-embedding/openwebui-citation.png" });
console.log("Citation panel pairing and prompt transfer passed");
await page.locator("#chat-input").fill("PDF資料を検索してください");
await page.locator("#chat-input").press("Enter");
await page.getByText("Synthetic pdf", { exact: true }).first().click();
const refs = JSON.parse(
  await readFile(".cache/viewer-embedding/refs.json", "utf8"),
);
await expect
  .poll(() =>
    page
      .frames()
      .some(
        (f) =>
          f.url().startsWith("http://127.0.0.1:18868/viewer?") &&
          f.url().includes(refs.pdf.source_id),
      ),
  )
  .toBe(true);
const pdfCitation = page
  .frames()
  .find(
    (f) =>
      f.url().startsWith("http://127.0.0.1:18868/viewer?") &&
      f.url().includes(refs.pdf.source_id),
  );
await expect(pdfCitation.getByLabel("接続コード")).toBeVisible();
await pdfCitation
  .getByRole("button", { name: "接続コードをチャット入力へ" })
  .click();
await expect(page.locator("#chat-input")).toContainText("接続コード");
await page.locator("#chat-input").press("Enter");
await expect(pdfCitation.locator("#detail")).toBeVisible({ timeout: 30000 });
await expect(
  pdfCitation.frameLocator("#original").locator("#pdfPage"),
).toHaveValue("1");
await expect(
  pdfCitation
    .frameLocator("#original")
    .frameLocator('.pdf-page[data-number="1"] iframe')
    .getByText("合成サンプル: 月別の件数", { exact: true }),
).toBeVisible();
await page.screenshot({
  path: "qa/viewer-embedding/openwebui-pdf-citation.png",
});
await page.locator("#chat-input").fill("PDFを開いて");
await page.locator("#chat-input").press("Enter");
await page.waitForTimeout(5000);
await page.getByText("request_document_view", { exact: true }).last().click();
await page.waitForTimeout(1200);
const rich = page.frameLocator("iframe[srcdoc]").last().frameLocator("#viewer");
await expect(rich.getByLabel("接続コード")).toBeVisible({ timeout: 15000 });
await rich.getByRole("button", { name: "接続コードをチャット入力へ" }).click();
await expect(page.locator("#chat-input")).toContainText("接続コード");
await page.locator("#chat-input").press("Enter");
await expect(rich.locator("#detail")).toBeVisible({ timeout: 30000 });
await expect(rich.frameLocator("#original").locator("#pdfPage")).toHaveValue(
  "2",
);
await expect(
  rich
    .frameLocator("#original")
    .frameLocator('.pdf-page[data-number="2"] iframe')
    .getByText("図と表の参照を確認する", { exact: true }),
).toBeVisible();
await page.screenshot({ path: "qa/viewer-embedding/openwebui-rich.png" });
const chatURL = page.url();
await page.reload();
await page.waitForTimeout(1000);
await page.getByText("request_document_view", { exact: true }).last().click();
await expect(
  page
    .frameLocator("iframe[srcdoc]")
    .last()
    .frameLocator("#viewer")
    .getByLabel("接続コード"),
).toBeVisible();
const chatId = new URL(chatURL).pathname.split("/").pop();
const api = async (path, token, body) => {
  const r = await fetch("http://127.0.0.1:18880" + path, {
    method: body ? "POST" : "GET",
    headers: {
      authorization: "Bearer " + token,
      "content-type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error("OWUI API status " + r.status);
  return r.json();
};
const chat = await api("/api/v1/chats/" + chatId, accounts[0].token);
const persisted = JSON.stringify(chat);
expect(persisted).not.toContain("/viewer/session/");
const copied = await api("/api/v1/chats/new", accounts[1].token, {
  chat: chat.chat,
});
const bobContext = await browser.newContext();
const bobPage = await bobContext.newPage();
await bobPage.goto("http://127.0.0.1:18880");
await bobPage.evaluate(
  (token) => localStorage.setItem("token", token),
  accounts[1].token,
);
await bobPage.goto("http://127.0.0.1:18880/c/" + copied.id);
if (
  await bobPage
    .getByRole("button", { name: "Okay, Let's Go!" })
    .isVisible()
    .catch(() => false)
)
  await bobPage.getByRole("button", { name: "Okay, Let's Go!" }).click();
await bobPage
  .getByText("request_document_view", { exact: true })
  .last()
  .click();
const bobRich = bobPage
  .frameLocator("iframe[srcdoc]")
  .last()
  .frameLocator("#viewer");
await expect(bobRich.getByLabel("接続コード")).toBeVisible();
await expect(bobRich.locator("#detail")).toHaveCount(0);
await writeFile(
  "qa/viewer-embedding/openwebui-results.json",
  JSON.stringify(
    {
      version: "0.11.4",
      citation_panel: true,
      citation_pdf_page: 1,
      rich_ui: true,
      pdf_page: 2,
      prompt_transfer: true,
      reload_requires_pairing: true,
      persisted_session_credentials: false,
      copied_chat_second_user_starts_disconnected: true,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  "Real Open WebUI citation/Rich UI/reload/copied-chat checks passed",
);
await bobContext.close();
await browser.close();
