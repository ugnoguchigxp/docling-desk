import { expect, test } from "vitest";
import {
  chunk,
  elements,
  explanationResult,
  explanations,
  policy,
  slides,
  translationPanel,
  translations,
  tables,
  library,
} from "./contracts";

test("invalid network structures fail before reaching document controls", () => {
  expect(() =>
    slides({ slides: [{ number: 1, width: "wide", height: 300 }] }),
  ).toThrow("応答形式");
  expect(() => tables([{ ref: "r", rows: [[{ html: "unsafe" }]] }])).toThrow(
    "応答形式",
  );
  expect(() => translationPanel({ available: true, texts: [null] })).toThrow(
    "応答形式",
  );
  expect(
    translationPanel({
      available: true,
      texts: ["<script>literal text</script>"],
    }).texts[0],
  ).toBe("<script>literal text</script>");
});

test("rejects invalid dates and counts before a library render can crash", () => {
  const valid = {
    id: "file",
    filename: "file.pdf",
    folder_id: null,
    state: "success",
    created: 100,
    duration: null,
    pages: 1,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: false,
  };
  expect(library({ folders: [], jobs: [valid] }).jobs[0].filename).toBe(
    "file.pdf",
  );
  for (const invalid of [
    { created: null },
    { created: 9e12 },
    { pages: -1 },
    { folder_id: undefined },
    { state: "unknown" },
    { translations: { en: { saved: "one", active: 0, failed: 0 } } },
  ])
    expect(() =>
      library({ folders: [], jobs: [{ ...valid, ...invalid }] }),
    ).toThrow("応答形式");
  expect(() =>
    library({
      jobs: [],
      folders: [{ id: "folder", name: "Folder", created: 0 }],
    }),
  ).toThrow("応答形式");
});

test("rejects impossible slide dimensions and out-of-range table headers", () => {
  for (const width of [0, -1, Infinity])
    expect(() =>
      slides({ slides: [{ number: 1, width, height: 300, preview: null }] }),
    ).toThrow("応答形式");
  expect(() =>
    tables([
      {
        ref: "r",
        label: "Table",
        source_label: "Page 1",
        pages: [1],
        columns: 1,
        rows: [["cell"]],
        header_rows: 2,
        merged: false,
      },
    ]),
  ).toThrow("応答形式");
});

const search = { queries: ["語"], evidence: [] };
const explanation = {
  created_at: "2026-01-01T00:00:00Z",
  extraction_state: "success",
  unlocated_count: 0,
  source: {
    kind: "page",
    excluded_pictures: 0,
    blocks: [{ id: "b", pages: [1], text: "本文" }],
    tables: [],
  },
  local_search: search,
  web_search: { ...search, status: "success" },
  explanation: {
    sections: [{ title: "節", text: "説明", source_ids: [], evidence_ids: [] }],
    glossary: [],
    supplements: [],
    limitations: [],
  },
};

test("accepts explanation, translation, element, chunk, and policy payloads", () => {
  expect(explanationResult({ result: explanation, source_match: true }).source_match).toBe(
    true,
  );
  expect(explanationResult({ result: null, source_match: false }).result).toBeNull();
  expect(() =>
    explanationResult({
      result: { ...explanation, web_search: search },
      source_match: true,
    }),
  ).toThrow("応答形式");
  expect(() =>
    explanationResult({
      result: { ...explanation, web_search: { status: "success" } },
      source_match: true,
    }),
  ).toThrow("応答形式");
  const record = {
    state: "completed",
    available: true,
    stale: false,
    error: null,
    result_created_at: null,
  };
  expect(
    translations({
      units: [
        {
          id: "u",
          kind: "slide",
          number: 1,
          mode: "panel",
          excluded_count: 0,
          segments_count: 1,
          languages: { en: record, ja: record },
        },
      ],
      unlocated_count: 0,
      profile: {},
      configuration_error: null,
    }).units,
  ).toHaveLength(1);
  expect(
    explanations({
      units: [],
      profile: {},
      enabled: false,
      source_error: null,
      configuration_error: null,
    }).enabled,
  ).toBe(false);
  expect(
    elements([
      {
        label: "本文",
        ref: "#/texts/0",
        text: "",
        pages: [],
        parent: null,
        captions: [],
        provenance: [{}],
      },
    ])[0].ref,
  ).toBe("#/texts/0");
  expect(
    chunk({
      id: "c",
      text: "本文",
      headings: [],
      pages: [1],
      refs: [],
      source: "a.pdf",
      source_sha256: "abc",
    }).id,
  ).toBe("c");
  expect(
    policy({
      context_chunks: 1,
      search_chunks: 2,
      docling_chunks: 3,
      target_chars: 2000,
    }).target_chars,
  ).toBe(2000);
});
