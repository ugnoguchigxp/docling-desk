import { test, expect } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";

test("Workspace original links open read-only Markdown, CSV and exact attachment bytes", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.UI_SYNTHETIC !== "1",
    "Requires the synthetic workspace",
  );
  const catalog = (await (await request.get("/api/wiki/catalog")).json())
    .articles;
  const original = catalog.find(
    (s: { external_key?: string; language?: string }) =>
      s.external_key === "requirements/R-1" && s.language === "original",
  );
  await page.goto(`/?mode=wiki&source=${original.id}`);
  const reader = page.locator(".wiki-body");
  await reader
    .getByRole("link", { name: "欠落原本", exact: true })
    .click({ force: true });
  await expect(page.locator(".wiki-message")).toContainText(
    "原本が見つからない",
  );
  const [raw] = await Promise.all([
    page.waitForEvent("popup"),
    reader.getByRole("link", { name: "原本Markdown", exact: true }).click(),
  ]);
  await expect(raw.getByText("書き出し原本（読み取り専用）")).toBeVisible();
  await expect(raw.locator("main")).toContainText("書き出し原本");
  const [csv] = await Promise.all([
    raw.waitForEvent("popup"),
    raw.getByRole("link", { name: "CSV", exact: true }).click(),
  ]);
  await expect(csv.locator("main table")).toContainText("原本");
  const [download] = await Promise.all([
    raw.waitForEvent("download"),
    raw.getByRole("link", { name: "原本を取得", exact: true }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("原本 メモ.md");
  expect(await readFile((await download.path())!)).toEqual(
    Buffer.from("# 書き出し原本\r\n\r\n[CSV](下位/目次%20一覧.csv)\r\n"),
  );
  const [attachment] = await Promise.all([
    page.waitForEvent("popup"),
    reader.getByRole("link", { name: "添付ファイル", exact: true }).click(),
  ]);
  await expect(
    attachment.getByText("この添付ファイルは取得して開いてください。"),
  ).toBeVisible();
  const [binary] = await Promise.all([
    attachment.waitForEvent("download"),
    attachment.getByRole("link", { name: "原本を取得", exact: true }).click(),
  ]);
  expect(await readFile((await binary.path())!)).toEqual(
    Buffer.from([0, 255, ...Buffer.from("synthetic-attachment\r\n")]),
  );
  await raw.close();
  await csv.close();
  await attachment.close();
});

test("CSV contents opens a page and unpublished Japanese displays original", async ({
  page,
}) => {
  await page.goto("/?mode=wiki");
  await page.getByRole("button", { name: "Markdownを取り込む" }).click();
  const dialog = page.locator("#wikiImport");
  await dialog.getByLabel("分類名").fill("CSV検証");
  const manifest = {
    articles: {
      "original.md": {
        language: "original",
        translation_group: "csv-test",
        translation_status: "untranslated",
      },
      "ja.md": {
        language: "ja",
        translation_group: "csv-test",
        translation_status: "untranslated",
      },
    },
  };
  await dialog.getByLabel("WikiのMarkdownファイル").setInputFiles([
    {
      name: "index.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("id,title,page_path\nR-1,原文の記事,ja.md\n"),
    },
    {
      name: "original.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "---\ntitle: 原文の記事\n---\n# 原文の記事\n\n実際の原文本文です。",
      ),
    },
    {
      name: "ja.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# 準備ページ\n\n未公開の準備本文です。"),
    },
    {
      name: "wiki-manifest.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(manifest)),
    },
  ]);
  await dialog.getByRole("button", { name: "取り込む", exact: true }).click();
  await expect(dialog).toBeHidden();
  const reader = page.locator(".wiki-body");
  await expect(reader.locator("table")).toContainText("R-1");
  await reader.getByRole("link", { name: "原文の記事" }).click();
  await expect(
    reader.getByRole("heading", { name: "原文の記事" }),
  ).toBeVisible();
  await expect(
    page.getByText("日本語訳は未公開です。原文を表示しています。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(reader).not.toContainText("準備本文");
  await expect(page.getByLabel("記事の言語")).toContainText("ja（未翻訳）");
});

test("Wiki import, table of contents, links, mode history, and removal", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator(".wiki-screen")).toBeVisible();
  await expect(page.locator("#library")).toBeHidden();
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "資料一覧", exact: true })
    .click();
  await expect(page).toHaveURL(/mode=library/);
  await page.reload();
  await expect(page.locator("#library")).toBeVisible();
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "Wiki", exact: true })
    .click();
  await expect(page).toHaveURL(/mode=wiki/);
  await page.getByRole("button", { name: "Markdownを取り込む" }).click();
  const dialog = page.locator("#wikiImport");
  await dialog.getByLabel("分類名").fill("ブラウザー検証");
  await dialog.getByLabel("WikiのMarkdownファイル").setInputFiles([
    {
      name: "guide.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# Wiki利用ガイド\n\nWikiの本文です。\n\n## 検索と出典\n\n記事と資料をまとめて検索します。\n\n| 項目 | 値 |\n| --- | --- |\n| 件数 | 120 |\n\n[次の記事](other.md#詳しい説明)\n\n<script>alert('unsafe')</script>\n",
      ),
    },
    {
      name: "other.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# 関連する記事\n\n## 詳しい説明\n\n安全な記事間リンクです。",
      ),
    },
  ]);
  await dialog.getByRole("button", { name: "取り込む", exact: true }).click();
  await expect(dialog).toBeHidden();
  const reader = page.locator(".wiki-body");
  await expect(
    reader.getByRole("heading", { name: "Wiki利用ガイド" }),
  ).toBeVisible();
  await expect(reader.locator("table")).toContainText("120");
  await expect(reader.locator("script")).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "記事の目次" })
    .getByRole("link", { name: "検索と出典" })
    .click();
  await expect(page).toHaveURL(/section=h-/);
  await page.reload();
  await expect(
    reader.getByRole("heading", { name: "検索と出典" }),
  ).toBeVisible();
  await mkdir("../.cache/wiki-qa", { recursive: true });
  await page.screenshot({ path: "../.cache/wiki-qa/wiki-desktop.png" });
  await reader.getByRole("link", { name: "次の記事" }).click();
  await expect(
    reader.getByRole("heading", { name: "詳しい説明" }),
  ).toBeVisible();
  const current = page.url();
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "資料一覧", exact: true })
    .click();
  await expect(page.locator("#library")).toBeVisible();
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "Wiki", exact: true })
    .click();
  await expect(page).toHaveURL(current);
  await page.getByRole("button", { name: "記事を削除", exact: true }).click();
  await page.getByRole("button", { name: "削除する", exact: true }).click();
  await expect(page.locator(".wiki-catalog")).not.toContainText("関連する記事");
  expect(errors).toEqual([]);
});

test("Shared text search returns document and Wiki citations, then opens the source", async ({
  page,
  request,
}) => {
  const imported = await request.post("/api/wiki/import", {
    multipart: {
      namespace: "検索検証",
      files: {
        name: "search.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "# 検索用の記事\n\n合成サンプルに関するWikiの説明です。",
        ),
      },
    },
  });
  expect(imported.status()).toBe(201);
  await page.goto("/");
  await page.getByRole("button", { name: "本文を検索", exact: true }).click();
  const dialog = page.locator("#knowledgeSearch");
  await dialog.getByLabel("検索文", { exact: true }).fill("合成サンプル");
  await dialog.getByRole("button", { name: "検索", exact: true }).click();
  const results = dialog.locator(".knowledge-results");
  await expect(
    results.getByRole("link", { name: /Wiki · 検索用の記事/ }),
  ).toBeVisible();
  await expect(
    results.getByRole("link", { name: /資料 ·/ }).first(),
  ).toBeVisible();
  await expect(results.getByRole("checkbox")).toHaveCount(0);
  await dialog.getByRole("button", { name: "RAG", exact: true }).click();
  await expect(dialog.getByRole("region", { name: "RAGの回答" })).toContainText(
    "本文にある用語",
  );
  await expect(
    dialog.getByRole("link", { name: /\[S1\].*本文を開く/ }),
  ).toBeVisible();
  await page.screenshot({ path: "../.cache/wiki-qa/shared-search.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog).toBeVisible();
  expect(
    await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth),
  ).toBe(true);
  await page.screenshot({ path: "../.cache/wiki-qa/rag-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await results
    .getByRole("link", { name: /資料 ·/ })
    .first()
    .click();
  await expect(dialog).toBeHidden();
  await expect(page.locator("#filename")).not.toBeEmpty();
  await expect(page).toHaveURL(/unit=/);
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "Wiki", exact: true })
    .click();
  await expect(page.locator(".wiki-screen")).toBeVisible();
});

test("Wiki menu and article remain usable on a narrow screen", async ({
  page,
  request,
}) => {
  const imported = await request.post("/api/wiki/import", {
    multipart: {
      namespace: "画面検証",
      files: {
        name: "mobile.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "# 狭い画面の記事\n\n## 手順\n\n本文を読むことができます。",
        ),
      },
    },
  });
  const source = (await imported.json()).articles[0].id;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/?mode=wiki&source=${source}`);
  await expect(
    page.getByRole("navigation", { name: "表示モード" }),
  ).toBeVisible();
  await expect(
    page.locator(".wiki-body").getByRole("heading", { name: "狭い画面の記事" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "本文を検索", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "../.cache/wiki-qa/wiki-mobile.png" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("Registered original and translated Markdown switch languages and open related material", async ({
  page,
  request,
}) => {
  const imported = await request.post("/api/wiki/import", {
    multipart: {
      namespace: "対訳検証",
      files: {
        name: "original.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Original article\n\nOriginal content"),
      },
      manifest: {
        name: "wiki-manifest.json",
        mimeType: "application/json",
        buffer: Buffer.from(
          JSON.stringify({
            articles: {
              "original.md": {
                language: "en",
                translation_group: "paired-guide",
                source_job_id: "9".repeat(32),
                source_unit: 2,
              },
            },
          }),
        ),
      },
    },
  });
  expect(imported.status()).toBe(201);
  const original = (await imported.json()).articles[0].id;
  const translated = await request.post("/api/wiki/import", {
    multipart: {
      namespace: "対訳検証",
      files: {
        name: "ja.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# 翻訳した記事\n\n登録された日本語本文"),
      },
      manifest: {
        name: "wiki-manifest.json",
        mimeType: "application/json",
        buffer: Buffer.from(
          JSON.stringify({
            articles: {
              "ja.md": { language: "ja", translation_group: "paired-guide" },
            },
          }),
        ),
      },
    },
  });
  expect(translated.status()).toBe(201);
  const japanese = (await translated.json()).articles[0].id;
  await page.goto(`/?mode=wiki&source=${original}`);
  await expect(page.locator(".wiki-body")).toContainText("Original content");
  await page.getByLabel("記事の言語").selectOption(japanese);
  await expect(page.locator(".wiki-body")).toContainText(
    "登録された日本語本文",
  );
  await page.getByLabel("記事の言語").selectOption(original);
  await page.getByRole("button", { name: /^関連資料：/ }).click();
  await expect(page.locator("#filename")).not.toBeEmpty();
  await expect(page).toHaveURL(/unit=2/);
});

test("Dropping Markdown in Wiki imports an article without uploading a document", async ({
  page,
}) => {
  const uploads: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/upload")) uploads.push(request.url());
  });
  await page.goto("/?mode=wiki");
  await page.locator(".wiki-screen").evaluate((element) => {
    const transfer = new DataTransfer();
    transfer.items.add(
      new File(["# ドロップした記事\n\nWikiの本文です。"], "drop.md", {
        type: "text/markdown",
      }),
    );
    transfer.items.add(
      new File(["%PDF-test"], "attachment.pdf", { type: "application/pdf" }),
    );
    element.dispatchEvent(
      new DragEvent("dragover", {
        dataTransfer: transfer,
        bubbles: true,
        cancelable: true,
      }),
    );
    element.dispatchEvent(
      new DragEvent("drop", {
        dataTransfer: transfer,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
  const dialog = page.locator("#wikiImport");
  await expect(dialog).toBeVisible();
  await expect(page.locator("#uploadDialog")).toBeHidden();
  await expect(dialog).toContainText(
    "Markdown記事・CSV目次・manifest以外の資料は取り込み対象から除外しました。",
  );
  await dialog.getByRole("button", { name: "取り込む", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".wiki-body")).toContainText("ドロップした記事");
  expect(uploads).toEqual([]);
});

test("Completed RAG answers revalidate deleted sources and hide outdated answers", async ({
  page,
  request,
}) => {
  const imported = await request.post("/api/wiki/import", {
    multipart: {
      namespace: "再検証",
      files: {
        name: "revalidate.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# 再検証記事\n\nunique-revalidation-text"),
      },
    },
  });
  const source = (await imported.json()).articles[0].id;
  let searches = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/api/knowledge/search"))
      searches++;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "本文を検索", exact: true }).click();
  const dialog = page.locator("#knowledgeSearch");
  await dialog
    .getByLabel("検索文", { exact: true })
    .fill("unique-revalidation-text");
  await dialog.getByRole("button", { name: "検索", exact: true }).click();
  await expect(dialog.locator(".knowledge-results li")).toHaveCount(1);
  await dialog.getByRole("button", { name: "RAG", exact: true }).click();
  await expect(dialog.getByRole("region", { name: "RAGの回答" })).toBeVisible();
  expect((await request.delete("/api/wiki/sources/" + source)).status()).toBe(
    200,
  );
  await expect(dialog.locator(".knowledge-results li")).toHaveCount(0);
  await expect(dialog.getByRole("region", { name: "RAGの回答" })).toHaveCount(
    0,
  );
  await expect(dialog).toContainText("出典が更新されました");
  expect(searches).toBe(1);
});

test("An unavailable current scope resets visibly after switching modes", async ({
  page,
  request,
}) => {
  const imported = await request.post("/api/wiki/import", {
    multipart: {
      namespace: "範囲検証",
      files: {
        name: "scope.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# 範囲記事\n\nscope-test-body"),
      },
    },
  });
  const source = (await imported.json()).articles[0].id;
  await page.goto(`/?mode=wiki&source=${source}`);
  await page.getByRole("button", { name: "本文を検索", exact: true }).click();
  const dialog = page.locator("#knowledgeSearch");
  await dialog
    .getByRole("combobox", { name: "範囲", exact: true })
    .selectOption("current");
  await dialog
    .getByRole("button", { name: "閉じる", exact: true })
    .last()
    .click();
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "資料一覧", exact: true })
    .click();
  await page.getByRole("button", { name: "本文を検索", exact: true }).click();
  await expect(
    dialog.getByRole("combobox", { name: "範囲", exact: true }),
  ).toHaveValue("all");
});

test("Closing an in-flight search prevents its late response from taking over", async ({
  page,
  request,
}) => {
  await request.post("/api/wiki/import", {
    multipart: {
      namespace: "遅延検証",
      files: {
        name: "late.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# 遅延応答記事\n\nlate-response-test"),
      },
    },
  });
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let received!: () => void;
  const ready = new Promise<void>((resolve) => {
    received = resolve;
  });
  await page.route("**/api/knowledge/search", async (route) => {
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "本文を検索", exact: true }).click();
  const dialog = page.locator("#knowledgeSearch");
  await dialog.getByLabel("検索文", { exact: true }).fill("late-response-test");
  await dialog.getByRole("button", { name: "検索", exact: true }).click();
  await ready;
  await dialog
    .getByRole("button", { name: "閉じる", exact: true })
    .last()
    .click();
  await page.getByRole("button", { name: "本文を検索", exact: true }).click();
  release();
  await expect(
    dialog.getByRole("button", { name: "検索", exact: true }),
  ).toBeEnabled();
  await expect(dialog.locator(".knowledge-results li")).toHaveCount(0);
  await dialog
    .getByLabel("検索文", { exact: true })
    .fill("no-match-after-late-response");
  await dialog.getByRole("button", { name: "検索", exact: true }).click();
  await expect(dialog).toContainText("該当する本文はありません");
});

test("Wiki import finishing after a history change keeps the current screen", async ({
  page,
}) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let received!: () => void;
  const ready = new Promise<void>((resolve) => {
    received = resolve;
  });
  await page.route("**/api/wiki/import", async (route) => {
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  await page.goto("/?mode=wiki");
  await page.getByRole("button", { name: "Markdownを取り込む" }).click();
  const dialog = page.locator("#wikiImport");
  await dialog.getByLabel("WikiのMarkdownファイル").setInputFiles({
    name: "history.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 履歴切り替え記事\n本文"),
  });
  await dialog.getByRole("button", { name: "取り込む", exact: true }).click();
  await ready;
  await page.evaluate(() => {
    history.pushState(null, "", "/?mode=library");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(dialog).toBeHidden();
  release();
  await expect(page.locator("#library")).toBeVisible();
  await expect(page).toHaveURL(/mode=library/);
  await page
    .getByRole("navigation", { name: "表示モード" })
    .getByRole("button", { name: "Wiki", exact: true })
    .click();
  await expect(page.locator(".wiki-catalog")).toContainText("履歴切り替え記事");
});

for (const [name, id, selector] of [
  ["PDF", "9".repeat(32), "#pdfPage"],
  ["XLSX", "8".repeat(32), "#sheet-tab-2"],
] as const) {
  test(`${name} deep-link position is applied once and manual navigation persists`, async ({
    page,
  }) => {
    await page.goto(`/?job=${id}&unit=2`);
    await expect(page.locator("#filename")).not.toBeEmpty();
    const frame = page.frameLocator("#original");
    if (name === "PDF") {
      await expect(frame.locator(selector)).toHaveValue("2");
      await frame.locator("#pdfPrevious").click();
      await expect(frame.locator(selector)).toHaveValue("1");
    } else {
      await expect(frame.locator(selector)).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await frame.locator("#sheet-tab-1").click();
    }
    await page.locator("#tab-structure").click();
    await page.locator("#tab-preview").click();
    await page.locator("#translationLanguage").selectOption("en");
    await page.locator("#translationLanguage").selectOption("original");
    if (name === "PDF") await expect(frame.locator(selector)).toHaveValue("1");
    else
      await expect(frame.locator("#sheet-tab-1")).toHaveAttribute(
        "aria-selected",
        "true",
      );
  });
}
