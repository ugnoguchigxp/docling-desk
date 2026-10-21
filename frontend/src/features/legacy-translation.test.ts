import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// The rollback UI (DOCLING_LEGACY_UI=1, /legacy/) still ships this script.
const script = readFileSync(
  resolve(process.cwd(), "../src/docling_desk/resources/static/translation.js"),
  "utf8",
);
const ids = [
  "translationStatus",
  "translationPanelTitle",
  "translationPanelText",
  "translationUnitList",
  "downloads",
  "translateOpen",
  "translationDialogClose",
  "translationPanelClose",
  "translationDialog",
  "translationUnits",
  "translationForm",
];

function overview(language: Record<string, unknown>, mode = "replace") {
  const idle = { state: "untranslated", available: false, stale: false };
  return {
    units: [
      {
        id: "page-1",
        kind: "page",
        number: 1,
        mode,
        excluded_count: 0,
        segments_count: 2,
        languages: { en: { ...idle, ...language }, ja: idle },
      },
    ],
    unlocated_count: 0,
    profile: {},
  };
}

async function show(units: unknown, result: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(String(url).endsWith("/translations") ? units : result),
    ),
  );
  const language = document.getElementById(
    "translationLanguage",
  ) as HTMLSelectElement;
  window.eval(script);
  const browser = (
    window as unknown as {
      TranslationBrowser: { setJob(job: object): void };
    }
  ).TranslationBrowser;
  browser.setJob({ id: "job", filename: "a.pdf", state: "success" });
  language.value = "en";
  language.dispatchEvent(new Event("change"));
  await vi.waitFor(() =>
    expect(document.getElementById("translationPanel")!.hidden).toBe(false),
  );
}

beforeEach(() => {
  document.body.innerHTML =
    ids.map((id) => `<div id="${id}"></div>`).join("") +
    '<select id="translationLanguage"><option value="original"></option><option value="en"></option></select>' +
    '<select id="translationScope"><option></option></select>' +
    '<aside id="translationPanel" hidden></aside><iframe id="original"></iframe>';
  document.getElementById("translationUnits")!.innerHTML = "<legend></legend>";
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>).TranslationBrowser;
});

it("lists a translation saved against an older page layout beside its original", async () => {
  await show(
    overview({ state: "completed", stale: true, result_created_at: "x" }),
    {
      available: false,
      legacy: true,
      pairs: [{ source: "Original A", translation: "訳A" }],
    },
  );
  const pair = document.querySelector(
    "#translationPanelText .translation-pair",
  )!;
  expect(pair.children[0].textContent).toBe("Original A");
  expect(pair.children[1].textContent).toBe("訳A");
  expect(
    document.getElementById("translationPanelTitle")!.textContent,
  ).toContain("原文と対訳");
  expect(document.getElementById("translationStatus")!.textContent).toContain(
    "以前の表示方式",
  );
});

it("shows original and translation together in the extracted-text panel", async () => {
  await show(
    overview(
      { state: "completed", available: true, result_created_at: "s" },
      "panel",
    ),
    {
      available: true,
      texts: ["Hello"],
      pairs: [{ source: "こんにちは", translation: "Hello" }],
    },
  );
  const texts = [...document.querySelectorAll("#translationPanelText p")].map(
    (p) => p.textContent,
  );
  expect(texts).toEqual(["こんにちは", "Hello"]);
});

it("keeps the plain list when the server sends no pairs", async () => {
  await show(
    overview(
      { state: "completed", available: true, result_created_at: "s" },
      "panel",
    ),
    { available: true, texts: ["Hello", "World"] },
  );
  expect(
    [...document.querySelectorAll("#translationPanelText p")].map(
      (p) => p.textContent,
    ),
  ).toEqual(["Hello", "World"]);
});
