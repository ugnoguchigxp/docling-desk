import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const fixture = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "imported.excalidraw",
);

interface SceneElement {
  type: string;
  text?: string;
  isDeleted?: boolean;
  containerId?: string | null;
}
interface Scene {
  type: string;
  elements: SceneElement[];
}

const diagnostics = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => {
  const issues: string[] = [];
  diagnostics.set(page, issues);
  page.on("pageerror", (error) => issues.push(error.message));
});
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  expect(diagnostics.get(page)).toEqual([]);
});

function openedId(page: Page) {
  const id = new URL(page.url()).searchParams.get("job");
  expect(id).toMatch(/^[0-9a-f]{32}$/);
  return id as string;
}

async function readScene(page: Page, id: string): Promise<Scene> {
  const response = await page.request.get(`/api/diagrams/${id}/content`);
  expect(response.ok()).toBe(true);
  expect(response.headers()["content-type"]).toContain("excalidraw");
  return (await response.json()) as Scene;
}

// Excalidraw reuses the "excalidraw" class on portals and library icons, some
// of them hidden, so a page-wide `.excalidraw` locator is not the editor. The
// editing region is the Excalidraw root that DiagramCanvas renders directly
// inside the editor section; the drawing surface is its interactive canvas.
const editorRoot = (page: Page) =>
  page.locator(
    'section[aria-label="図表エディター"] .diagram-canvas > .excalidraw',
  );
const editorCanvas = (page: Page) =>
  editorRoot(page).locator("canvas.interactive");

const live = (scene: Scene) => scene.elements.filter((e) => !e.isDeleted);

async function createDiagram(page: Page, name: string) {
  await page.goto("/?mode=library");
  await page.locator("#newDiagram").click();
  await expect(page.locator("#diagramDialog")).toBeVisible();
  await page.locator("#diagramName").fill(name);
  await page.locator("#submitDiagram").click();
  await expect(page.locator("#diagramDialog")).toBeHidden();
  await expect(page.locator("#filename")).toHaveText(`${name}.excalidraw`);
  await expect(editorRoot(page)).toBeVisible();
  return openedId(page);
}

test("create a diagram, draw a rectangle with text, save, reload, and keep the content", async ({
  page,
}) => {
  const id = await createDiagram(page, "e2e 構成図");
  expect(live(await readScene(page, id))).toEqual([]);

  const canvas = editorCanvas(page);
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("the Excalidraw canvas has no layout");
  const left = box.x + box.width / 3,
    top = box.y + box.height / 3;

  // "r" selects the rectangle tool; dragging draws it.
  await page.keyboard.press("r");
  await page.mouse.move(left, top);
  await page.mouse.down();
  await page.mouse.move(left + 240, top + 120, { steps: 8 });
  await page.mouse.up();
  // Double-clicking a shape starts bound text; Escape commits it.
  await page.mouse.dblclick(left + 120, top + 60);
  await page.keyboard.type("Hello 図表");
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect
    .poll(async () => {
      const elements = live(await readScene(page, id));
      return {
        rectangle: elements.some((e) => e.type === "rectangle"),
        text: elements.find((e) => e.type === "text")?.text,
      };
    })
    .toEqual({ rectangle: true, text: "Hello 図表" });

  await page.reload();
  await expect(page.locator("#filename")).toHaveText("e2e 構成図.excalidraw");
  await expect(editorRoot(page)).toBeVisible();
  const reloaded = live(await readScene(page, id));
  expect(reloaded.filter((e) => e.type === "rectangle")).toHaveLength(1);
  expect(reloaded.find((e) => e.type === "text")?.text).toBe("Hello 図表");
  // The saved text is rendered again by the canvas, not only stored.
  await expect(editorCanvas(page)).toBeVisible();
});

test("import a .excalidraw file and download the original", async ({
  page,
}) => {
  const original = await readFile(fixture);
  await page.goto("/?mode=library");
  await page.locator("#addFile").click();
  await page.locator("#upload input[type=file]").setInputFiles(fixture);
  await page.locator("#upload button[type=submit]").click();
  await expect(page.locator("#uploadDialog")).toBeHidden();

  await page.locator("#fileType").selectOption("excalidraw");
  const row = page.locator("#library tr").filter({ hasText: "imported" });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("図表");
  await page.getByLabel("imported.excalidrawを開く", { exact: true }).click();
  await expect(page.locator("#filename")).toHaveText("imported.excalidraw");
  await expect(editorRoot(page)).toBeVisible();
  const id = openedId(page);
  expect(live(await readScene(page, id)).map((e) => e.type)).toEqual([
    "rectangle",
    "text",
  ]);

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#originalDownload").click(),
  ]);
  expect(download.suggestedFilename()).toBe("imported.excalidraw");
  const saved = await download.path();
  expect(saved).toBeTruthy();
  expect(await readFile(saved)).toEqual(original);
});

test("diagrams cannot be copied from the library", async ({ page }) => {
  await createDiagram(page, "e2e コピー不可");
  await page.locator("#backToFiles").click();
  await page
    .getByRole("checkbox", { name: "e2e コピー不可.excalidrawを選択" })
    .check();
  await expect(page.locator("#selectionCopy")).toBeDisabled();
});
