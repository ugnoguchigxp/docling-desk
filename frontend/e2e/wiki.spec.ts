import { test, expect, type Locator } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { PNG } from "pngjs";
import { createHash } from "node:crypto";

function testImage(red = 32, green = 115, blue = 140) {
  const png = new PNG({ width: 480, height: 220 });
  for (let i = 0; i < png.data.length; i += 4) {
    png.data[i] = red;
    png.data[i + 1] = green;
    png.data[i + 2] = blue;
    png.data[i + 3] = 255;
  }
  return PNG.sync.write(png);
}

async function dropImages(target: Locator, colors = [[32, 115, 140]]) {
  const transfer = await target.page().evaluateHandle(
    (files) => {
      const data = new DataTransfer();
      for (const [index, bytes] of files.entries())
        data.items.add(
          new File([new Uint8Array(bytes)], `drop-${index}.png`, {
            type: "image/png",
          }),
        );
      return data;
    },
    colors.map((color) =>
      Array.from(testImage(...(color as [number, number, number]))),
    ),
  );
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (!box) throw new Error("Drop target is not visible");
  for (const type of ["dragenter", "dragover", "drop"])
    await target.dispatchEvent(type, {
      dataTransfer: transfer,
      clientX: box.x + 40,
      clientY: box.y + 2,
    });
  await transfer.dispose();
}

test("Dropped Plate images enter the upload folder at the drop position and survive reload", async ({
  page,
  request,
}) => {
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  await page
    .getByLabel("記事タイトル", { exact: true })
    .fill("ドロップ画像の記事");
  await page
    .getByLabel("記事パス", { exact: true })
    .fill("writing/dropped-images.md");
  const rich = page.getByLabel("本文ブロック", { exact: true });
  await rich.fill("前半の本文");
  await rich.press("Enter");
  await rich.pressSequentially("後半の本文");
  // Finish the second upload first: network order must not reorder the article.
  let releaseFirst = () => {};
  const secondFinished = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  let sent = 0;
  await page.route("**/api/upload", async (route) => {
    const index = sent++;
    const response = await route.fetch();
    if (index === 0) await secondFinished;
    await route.fulfill({ response });
    if (index === 1) releaseFirst();
  });
  // Keep the selection elsewhere: insertion must follow the drop target.
  await rich.getByText("前半の本文", { exact: true }).click();
  await dropImages(
    rich.locator(".slate-blockWrapper").filter({ hasText: "後半の本文" }),
    [
      [32, 115, 140],
      [170, 90, 40],
    ],
  );
  const images = rich.locator('img[src*="/api/wiki/images/"]');
  await expect(images).toHaveCount(2);
  const urls = await images.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("src")),
  );
  expect(urls).toEqual(
    [
      [32, 115, 140],
      [170, 90, 40],
    ].map(
      (color) =>
        `/api/wiki/images/${createHash("sha256")
          .update(testImage(...(color as [number, number, number])))
          .digest("hex")}.png`,
    ),
  );
  await expect
    .poll(() =>
      images.evaluateAll((nodes) =>
        nodes.every(
          (node) =>
            node instanceof HTMLImageElement &&
            node.complete &&
            node.naturalWidth === 480,
        ),
      ),
    )
    .toBe(true);
  const library = await (await request.get("/api/library")).json();
  const folder = library.folders.find(
    (item: { name: string; parent_id: string | null }) =>
      item.name === "upload" && item.parent_id === null,
  );
  expect(folder).toBeTruthy();
  for (const url of urls) {
    const jobs = library.jobs.filter(
      (job: { folder_id: string; image?: { reference: string } }) =>
        job.folder_id === folder.id && job.image?.reference === url,
    );
    expect(jobs).toHaveLength(1);
  }
  await expect(page.locator("#uploadDialog")).toBeHidden();
  await expect(
    page.getByText("記事の編集を閉じてから、Markdownを取り込んでください。", {
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(page.locator(".wiki-cover-preview")).toHaveAttribute(
    "src",
    urls[0]!,
  );
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await expect(page.locator(".wiki-body")).toContainText("後半の本文");
  const sid = new URL(page.url()).searchParams.get("source");
  const saved = await (
    await request.get(`/api/wiki/sources/${sid}/edit`)
  ).json();
  const parts = saved.editor_document.value.map(
    (node: { type: string; url?: string; children: { text: string }[] }) =>
      node.type === "img"
        ? node.url
        : node.children.map((child) => child.text).join(""),
  );
  expect(parts.slice(0, 4)).toEqual(["前半の本文", ...urls, "後半の本文"]);
  expect(saved.article_info.image.url).toBe(urls[0]);
  await page.reload();
  await page.getByRole("button", { name: "編集", exact: true }).click();
  await expect(rich.locator('img[src*="/api/wiki/images/"]')).toHaveCount(2);
  await mkdir("../qa/wiki-authoring", { recursive: true });
  await page.screenshot({
    path: "../qa/wiki-authoring/library-drop.png",
    fullPage: true,
  });
});

test("A failed Plate image upload can be retried into an existing upload folder", async ({
  page,
  request,
}) => {
  const library = await (await request.get("/api/library")).json();
  const existing = library.folders.find(
    (folder: { name: string; parent_id: string | null }) =>
      folder.name === "upload" && folder.parent_id === null,
  );
  const folder =
    existing ??
    (await (
      await request.post("/api/folders", { data: { name: "upload" } })
    ).json());
  let attempts = 0;
  const folderCreates: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().endsWith("/api/folders"))
      folderCreates.push(req.url());
  });
  await page.route("**/api/upload", async (route) => {
    if (attempts++ === 0)
      await route.fulfill({
        status: 507,
        json: { detail: "試験用：画像を保存できませんでした。" },
      });
    else await route.continue();
  });
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  const rich = page.getByLabel("本文ブロック", { exact: true });
  await rich.fill("本文は失いません。");
  await dropImages(rich.locator(".slate-blockWrapper"), [[70, 120, 30]]);
  await expect(
    page.getByText("試験用：画像を保存できませんでした。", { exact: true }),
  ).toBeVisible();
  await expect(rich.locator('img[src*="/api/wiki/images/"]')).toHaveCount(0);
  await expect(rich).toContainText("本文は失いません。");
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    rich.getByText("Add an image", { exact: true }).click(),
  ]);
  await chooser.setFiles({
    name: "retry-image.png",
    mimeType: "image/png",
    buffer: testImage(70, 120, 30),
  });
  const image = rich.locator('img[src*="/api/wiki/images/"]');
  await expect(image).toHaveCount(1);
  const imageUrl = await image.getAttribute("src");
  expect(attempts).toBe(2);
  expect(folderCreates).toHaveLength(0);
  const after = await (await request.get("/api/library")).json();
  expect(
    after.jobs.filter(
      (job: { folder_id: string; filename: string }) =>
        job.folder_id === folder.id && job.filename === "retry-image.png",
    ),
  ).toHaveLength(1);
  // Discarding the article must retain its successfully uploaded library image.
  await page.getByRole("button", { name: "← 閲覧に戻る", exact: true }).click();
  await page.getByRole("button", { name: "破棄して戻る", exact: true }).click();
  await expect(rich).toHaveCount(0);
  expect((await request.get(imageUrl!)).ok()).toBeTruthy();
});

test("The Plate image menu selects an existing library image without another upload", async ({
  page,
  request,
}) => {
  const folder = await (
    await request.post("/api/folders", { data: { name: "画像選択試験" } })
  ).json();
  const job = await (
    await request.post("/api/upload", {
      multipart: {
        folder_id: folder.id,
        file: {
          name: "library-existing.png",
          mimeType: "image/png",
          buffer: testImage(104, 40, 170),
        },
      },
    })
  ).json();
  const assetId = job.id.replace(
    /(.{8})(.{4})(.{4})(.{4})(.{12})/,
    "$1-$2-$3-$4-$5",
  );
  const asset = await (await request.get(`/api/v1/media/${assetId}`)).json();
  expect(
    (
      await request.patch(`/api/v1/media/${assetId}`, {
        headers: { "If-Match": asset.metadata_etag },
        data: {
          display_name: "選択用の画像.png",
          default_alt: "資料に保存した説明",
          description: "本文に再利用する図",
          tags: ["再利用"],
        },
      })
    ).ok(),
  ).toBeTruthy();
  const before = await (await request.get("/api/library")).json();
  const uploads: string[] = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      ["/api/upload", "/api/wiki/images"].some((path) =>
        req.url().endsWith(path),
      )
    )
      uploads.push(req.url());
  });
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  await page
    .getByLabel("記事タイトル", { exact: true })
    .fill("資料の画像を再利用");
  await page
    .getByLabel("記事パス", { exact: true })
    .fill("writing/library-image.md");
  const rich = page.getByLabel("本文ブロック", { exact: true });
  await rich.fill("挿入位置の前");
  await rich.press("Enter");
  await rich.pressSequentially("挿入位置の後");
  await rich.getByText("挿入位置の前", { exact: true }).click();
  await rich.press("End");
  await expect
    .poll(() =>
      page.evaluate(() => window.getSelection()?.anchorNode?.textContent),
    )
    .toBe("挿入位置の前");
  let opens = 0;
  const openLibrary = async () => {
    if (opens++ === 0)
      await page
        .getByRole("radio", { name: "Image", exact: true })
        .press("ArrowDown");
    else
      await page
        .getByRole("button", { name: "Imageの挿入方法", exact: true })
        .click();
    await page
      .getByRole("menuitem", { name: "資料一覧から選ぶ", exact: true })
      .click({ timeout: 15000 });
  };
  await openLibrary();
  const dialog = page.getByRole("dialog", {
    name: "資料一覧から画像を選ぶ",
    exact: true,
  });
  await dialog
    .getByRole("searchbox", { name: "資料の画像を検索" })
    .fill("見つからない画像名");
  await expect(
    dialog.getByText("条件に合う画像がありません。", { exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(rich.locator("img")).toHaveCount(0);
  await openLibrary();
  await dialog
    .getByRole("searchbox", { name: "資料の画像を検索" })
    .fill("再利用");
  await expect(dialog.getByText("画像選択試験", { exact: true })).toBeVisible();
  await mkdir("../qa/wiki-authoring", { recursive: true });
  await page.screenshot({
    path: "../qa/wiki-authoring/library-picker.png",
    fullPage: true,
  });
  await dialog
    .getByRole("button", { name: "選択用の画像.png", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  const image = rich.locator(`img[src="${job.image.reference}"]`);
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute("alt", "資料に保存した説明");
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await expect(page.locator(".wiki-body")).toContainText("挿入位置の後");
  const sid = new URL(page.url()).searchParams.get("source");
  const saved = await (
    await request.get(`/api/wiki/sources/${sid}/edit`)
  ).json();
  const parts = saved.editor_document.value.map(
    (node: { type: string; url?: string; children: { text: string }[] }) =>
      node.type === "img"
        ? node.url
        : node.children.map((child) => child.text).join(""),
  );
  expect(parts.slice(0, 3)).toEqual([
    "挿入位置の前",
    job.image.reference,
    "挿入位置の後",
  ]);
  expect(saved.article_info.image).toMatchObject({
    url: job.image.reference,
    alt: "資料に保存した説明",
  });
  await page.reload();
  await page.getByRole("button", { name: "編集", exact: true }).click();
  await expect(image).toHaveAttribute("alt", "資料に保存した説明");
  expect((await (await request.get("/api/library")).json()).jobs).toHaveLength(
    before.jobs.length,
  );
  expect(uploads).toHaveLength(0);
});

test("Create a Wiki article with Plate, retain article information, and edit it after reload", async ({
  page,
  request,
}) => {
  await page.goto("/?mode=wiki");
  const upload = page.getByRole("button", {
    name: "Markdownを取り込む",
    exact: true,
  });
  const create = page.getByRole("button", {
    name: "新しい記事を作成",
    exact: true,
  });
  await expect(upload).toHaveText("");
  await expect(create).toHaveText("");
  await create.click();
  await page
    .getByLabel("記事タイトル", { exact: true })
    .fill("Plateから作る記事");
  await page
    .getByLabel("概要・副題", { exact: true })
    .fill("複数媒体に向けた記事情報");
  await page
    .getByLabel("記事パス", { exact: true })
    .fill("writing/plate-new.md");
  const rich = page.getByLabel("本文ブロック", { exact: true });
  await rich.fill("Plateで書いた検索可能な本文です。");
  await page.getByRole("radio", { name: "Bold (⌘+B)", exact: true }).click();
  await rich.pressSequentially("強調");
  await page.getByLabel("キーワードを追加").fill("Markdown");
  await page.getByLabel("キーワードを追加").press("Enter");
  await page.getByLabel("キーワードを追加").fill("AI");
  await page
    .getByLabel("元記事の公開URL", { exact: true })
    .fill("https://example.com/original");
  for (const name of [
    "Underline (⌘+U)",
    "Strikethrough (⌘+⇧+M)",
    "Text color",
    "Background color",
    "Align",
    "Table",
    "Emoji",
    "Image",
    "Video",
    "Audio",
    "File",
    "Comment",
    "Editing mode",
  ]) {
    await expect(page.getByLabel(name, { exact: true })).toBeVisible();
  }
  await expect(page.getByLabel("代表画像のURL")).toHaveCount(0);
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("radio", { name: "Image", exact: true }).click(),
  ]);
  await chooser.setFiles({
    name: "cover.png",
    mimeType: "image/png",
    buffer: testImage(),
  });
  await expect(page.locator(".wiki-cover-preview")).toBeVisible();
  const bodyImage = page.locator('.wiki-plate img[src*="/api/wiki/images/"]');
  await bodyImage.click();
  await page.getByRole("button", { name: "Edit link", exact: true }).click();
  await page.getByLabel("代替テキスト", { exact: true }).fill("図の説明");
  await page.getByRole("button", { name: "完了", exact: true }).click();
  await bodyImage.click();
  await page.getByRole("button", { name: "Caption", exact: true }).click();
  await page.getByPlaceholder("Write a caption...").fill("画像の補足");
  await page.getByLabel("画像の出典", { exact: true }).fill("作者名");
  await page.getByRole("button", { name: "Markdown", exact: true }).click();
  await expect(page.getByLabel("Markdownソース")).toContainText("**強調**");
  await page.getByRole("button", { name: "見たまま", exact: true }).click();
  await expect(page.getByRole("button", { name: /投稿設定/ })).toBeDisabled();
  await mkdir("../qa/wiki-authoring", { recursive: true });
  await page.screenshot({
    path: "../qa/wiki-authoring/editor.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await expect(page.locator(".wiki-body")).toContainText(
    "Plateで書いた検索可能な本文です。",
  );
  await expect(page.locator(".wiki-body")).toContainText(
    "複数媒体に向けた記事情報",
  );
  const sid = new URL(page.url()).searchParams.get("source");
  const saved = await (
    await request.get(`/api/wiki/sources/${sid}/edit`)
  ).json();
  expect(saved.article_info).toMatchObject({
    subtitle: "複数媒体に向けた記事情報",
    keywords: ["Markdown", "AI"],
    original_url: "https://example.com/original",
    image: { alt: "図の説明", caption: "画像の補足", credit: "作者名" },
  });
  expect((await request.get(saved.article_info.image.url)).ok()).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page.getByLabel("概要・副題")).toHaveValue(
    "複数媒体に向けた記事情報",
  );
  await expect(page.locator(".wiki-cover-preview")).toHaveAttribute(
    "src",
    saved.article_info.image.url,
  );
  await expect(
    page.locator('.wiki-plate img[src*="/api/wiki/images/"]'),
  ).toHaveAttribute("alt", "図の説明");
  expect(
    saved.editor_document.value.some(
      (node: { type: string }) => node.type === "img",
    ),
  ).toBeTruthy();
  await page.getByLabel("概要・副題").fill("更新した副題");
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await page.reload();
  await expect(page.locator(".wiki-body")).toContainText("更新した副題");
  await page.screenshot({
    path: "../qa/wiki-authoring/saved.png",
    fullPage: true,
  });
  const found = await (
    await request.post("/api/knowledge/search", {
      data: { query: "検索可能", mode: "text" },
    })
  ).json();
  expect(
    found.output.results.some(
      (hit: { source_id: string }) => hit.source_id === sid,
    ),
  ).toBeTruthy();
});

test("Standard Plate formatting, tables and comments survive Wiki save and reload", async ({
  page,
  request,
}) => {
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  await page
    .getByLabel("記事タイトル", { exact: true })
    .fill("標準ツールバーの機能");
  await page
    .getByLabel("記事パス", { exact: true })
    .fill("writing/full-toolbar.md");
  const toolbar = page.getByRole("toolbar", { name: "Plate toolbar" });
  const rich = page.getByLabel("本文ブロック", { exact: true });
  await rich.fill("装飾とコメントを保持する本文");
  await rich.press("Meta+a");
  await toolbar
    .getByRole("radio", { name: "Underline (⌘+U)", exact: true })
    .click();
  await toolbar
    .getByRole("radio", { name: "Strikethrough (⌘+⇧+M)", exact: true })
    .click();
  await page.getByLabel("Font size", { exact: true }).fill("24");
  await page.getByLabel("Font size", { exact: true }).press("Enter");
  await toolbar.getByRole("radio", { name: "Align", exact: true }).click();
  await page
    .getByRole("menuitemradio", { name: "Align center", exact: true })
    .click();
  await toolbar.getByRole("button", { name: "Comment", exact: true }).click();
  await page
    .getByLabel("Comment text", { exact: true })
    .fill("保存するコメント");
  await page.getByRole("button", { name: "Send comment", exact: true }).click();
  await rich.click();
  await rich.press("Meta+End");
  await rich.press("ArrowRight");
  await rich.press("Enter");
  await toolbar.getByRole("radio", { name: "Table", exact: true }).click();
  await page.getByRole("menuitem", { name: "Table", exact: true }).hover();
  await page
    .getByRole("button", { name: "Insert 2 by 2 table", exact: true })
    .hover();
  await page
    .getByRole("button", { name: "Insert 2 by 2 table", exact: true })
    .click();
  await page.keyboard.press("Escape");
  await expect(rich.locator("table")).toHaveCount(1);
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await expect(page.locator(".wiki-body table")).toBeVisible();
  const sid = new URL(page.url()).searchParams.get("source");
  const saved = await (
    await request.get(`/api/wiki/sources/${sid}/edit`)
  ).json();
  expect(JSON.stringify(saved.editor_document.value)).toContain(
    '"underline":true',
  );
  expect(JSON.stringify(saved.editor_document.value)).toContain(
    '"strikethrough":true',
  );
  expect(JSON.stringify(saved.editor_document.value)).toContain(
    '"fontSize":"24px"',
  );
  expect(JSON.stringify(saved.editor_document.value)).toContain(
    '"align":"center"',
  );
  expect(JSON.stringify(saved.editor_document.discussions)).toContain(
    "保存するコメント",
  );
  await page.reload();
  await page.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page.getByLabel("本文ブロック").locator("table")).toBeVisible();
  await page.screenshot({
    path: "../qa/wiki-authoring/full-toolbar.png",
    fullPage: true,
  });
});

test("Standard toolbar exports Markdown, HTML and Word documents", async ({
  page,
}) => {
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  await page
    .getByLabel("本文ブロック", { exact: true })
    .fill("標準ツールバーから書き出す本文");
  const toolbar = page.getByRole("toolbar", { name: "Plate toolbar" });
  for (const [label, extension] of [
    ["Markdown", "md"],
    ["HTML", "html"],
    ["Word", "docx"],
  ]) {
    await toolbar.getByRole("radio", { name: "Export", exact: true }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page
        .getByRole("menuitem", { name: `Export as ${label}`, exact: true })
        .click(),
    ]);
    expect(download.suggestedFilename()).toBe(`plate.${extension}`);
    const bytes = await readFile((await download.path())!);
    expect(bytes.length).toBeGreaterThan(20);
    if (label === "Markdown" || label === "HTML")
      expect(bytes.toString()).toContain("標準ツールバーから書き出す本文");
    else expect(bytes.subarray(0, 2).toString()).toBe("PK");
  }
});

test("Standard AI menu consumes the provider stream and keeps generated text reviewable", async ({
  page,
}) => {
  await page.route("**/api/wiki/editor/ai", async (route) => {
    const events = [
      { type: "start", messageId: "test-message" },
      { type: "data-toolName", data: "generate", transient: true },
      { type: "start-step" },
      { type: "text-start", id: "text" },
      { type: "text-delta", id: "text", delta: "AIで追記した本文" },
      { type: "text-end", id: "text" },
      { type: "finish-step" },
      { type: "finish" },
    ];
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "x-vercel-ai-ui-message-stream": "v1",
      },
      body:
        events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("") +
        "data: [DONE]\n\n",
    });
  });
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  await page.getByLabel("本文ブロック", { exact: true }).fill("既存の本文。");
  await page.getByRole("button", { name: "AI commands", exact: true }).click();
  await page
    .getByRole("option", { name: "Continue writing", exact: true })
    .click();
  await expect(page.getByLabel("本文ブロック", { exact: true })).toContainText(
    "AIで追記した本文",
  );
  await expect(page.getByLabel("本文ブロック", { exact: true })).toContainText(
    "既存の本文。",
  );
});

test("Rich articles retain Wiki outline deep links and relative article links", async ({
  page,
  request,
}) => {
  const target = `wiki-${crypto.randomUUID().replaceAll("-", "")}`;
  const source = `wiki-${crypto.randomUUID().replaceAll("-", "")}`;
  const common = {
    namespace: "Wiki",
    title: "リンク先の記事",
    body: "リンク先の本文",
  };
  expect(
    (
      await request.put(`/api/wiki/sources/${target}`, {
        data: { ...common, path: "reader-target.md" },
      })
    ).ok(),
  ).toBeTruthy();
  const body = "# **見出し**\n\n[関連記事](reader-target.md)";
  const value = [
    { type: "h1", id: "heading", children: [{ text: "見出し", bold: true }] },
    {
      type: "p",
      children: [
        {
          type: "a",
          url: "reader-target.md",
          children: [{ text: "関連記事" }],
        },
      ],
    },
  ];
  expect(
    (
      await request.put(`/api/wiki/sources/${source}`, {
        data: {
          namespace: "Wiki",
          path: "reader-source.md",
          title: "リンク元の記事",
          body,
          editor_document: { version: 1, value, discussions: [] },
        },
      })
    ).ok(),
  ).toBeTruthy();
  const article = await (
    await request.get(`/api/wiki/sources/${source}`)
  ).json();
  const anchor = article.outline[0].anchor;
  await page.goto(`/?mode=wiki&source=${source}&section=${anchor}`);
  const heading = page
    .locator(".wiki-body")
    .getByRole("heading", { name: "見出し", exact: true });
  await expect(heading).toHaveAttribute("id", anchor);
  await expect(heading).toBeFocused();
  await page
    .locator(".wiki-body")
    .getByRole("link", { name: "関連記事", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`source=${target}`));
  await expect(page.locator(".wiki-body")).toContainText("リンク先の本文");
});

test("Standard drawing canvas saves actual shapes and reopens the drawing", async ({
  page,
  request,
}) => {
  await page.goto("/?mode=wiki");
  await page
    .getByRole("button", { name: "新しい記事を作成", exact: true })
    .click();
  await page.getByLabel("記事タイトル", { exact: true }).fill("描画の保存");
  await page.getByLabel("記事パス", { exact: true }).fill("drawing.md");
  await page.getByLabel("本文ブロック", { exact: true }).click();
  await page
    .getByRole("toolbar", { name: "Plate toolbar" })
    .getByRole("radio", { name: "Insert", exact: true })
    .first()
    .click();
  await page.getByRole("menuitem", { name: "Excalidraw", exact: true }).click();
  const canvas = page.locator(".excalidraw canvas.interactive");
  await expect(canvas).toBeVisible();
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x + 100, box.y + 100);
  await page.keyboard.press("r");
  await page.mouse.move(box.x + 120, box.y + 120);
  await page.mouse.down();
  await page.mouse.move(box.x + 250, box.y + 210, { steps: 5 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await expect(page.locator(".wiki-body .excalidraw")).toBeVisible();
  const sid = new URL(page.url()).searchParams.get("source");
  const saved = await (
    await request.get(`/api/wiki/sources/${sid}/edit`)
  ).json();
  expect(JSON.stringify(saved.editor_document.value)).toContain(
    '"type":"rectangle"',
  );
  expect(saved.body).toContain("<excalidraw");
  await page.reload();
  await page.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page.locator(".excalidraw canvas.interactive")).toBeVisible();
});

test("Featured image follows the first body image after reorder and deletion", async ({
  page,
  request,
}) => {
  const urls: string[] = [];
  for (const color of [
    [30, 120, 140],
    [170, 90, 40],
  ]) {
    const response = await request.post("/api/wiki/images", {
      multipart: {
        file: {
          name: "body.png",
          mimeType: "image/png",
          buffer: testImage(...(color as [number, number, number])),
        },
      },
    });
    expect(response.ok()).toBeTruthy();
    urls.push((await response.json()).url);
  }
  const id = `wiki-${crypto.randomUUID().replaceAll("-", "")}`;
  const response = await request.put(`/api/wiki/sources/${id}`, {
    data: {
      namespace: "Wiki",
      path: "writing/image-order.md",
      title: "画像の順序",
      body: `本文\n\n![最初](${urls[0]})\n\n![次](${urls[1]})`,
    },
  });
  expect(response.ok()).toBeTruthy();
  await page.goto(`/?mode=wiki&source=${id}`);
  await page.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page.locator(".wiki-cover-preview")).toHaveAttribute(
    "src",
    urls[0],
  );
  await page.getByRole("button", { name: "Markdown", exact: true }).click();
  await page
    .getByLabel("Markdownソース")
    .fill(`本文\n\n![次](${urls[1]})\n\n![最初](${urls[0]})`);
  await page.getByRole("button", { name: "見たまま", exact: true }).click();
  await expect(page.locator(".wiki-cover-preview")).toHaveAttribute(
    "src",
    urls[1],
  );
  for (const expected of [urls[0], null]) {
    await page
      .locator('.wiki-plate img[src*="/api/wiki/images/"]')
      .first()
      .click();
    await page
      .getByRole("button", { name: "Delete media", exact: true })
      .click();
    if (expected)
      await expect(page.locator(".wiki-cover-preview")).toHaveAttribute(
        "src",
        expected,
      );
    else await expect(page.locator(".wiki-cover-preview")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Wikiに保存", exact: true }).click();
  await expect(page.locator(".wiki-body")).toContainText("本文");
  const saved = await (
    await request.get(`/api/wiki/sources/${id}/edit`)
  ).json();
  expect(saved.article_info.image.url).toBe("");
  expect(saved.body).not.toContain("![");
});

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
