import { describe, expect, it } from "vitest";
import type { Route } from "../../lib/route";
import { hitRoute, hitUrl, searchSnippet } from "./hit";
import type { Hit } from "./types";

const route = {
  mode: "library",
  job: "job-1",
  source: null,
  section: null,
  folder: "folder-9",
  view: "preview",
  unit: 2,
} as Route;

const base: Hit = {
  chunk_id: "c1",
  source_id: "wiki-1",
  kind: "wiki",
  title: "記事",
  text: "",
  job_id: null,
  unit: 1,
  partial: false,
  locator: {},
};

describe("hit navigation", () => {
  it("opens a Wiki hit at its heading and keeps the rest of the route", () => {
    const hit = { ...base, locator: { anchor: "sec-2" } };
    expect(hitRoute(route, hit)).toEqual({
      ...route,
      mode: "wiki",
      source: "wiki-1",
      section: "sec-2",
    });
    expect(hitUrl(hit)).toBe("/?mode=wiki&source=wiki-1&section=sec-2");
    expect(hitUrl(base)).toBe("/?mode=wiki&source=wiki-1");
  });

  it("opens a document hit in the preview at its unit", () => {
    const hit: Hit = { ...base, kind: "document", job_id: "job-7", unit: 5 };
    expect(hitRoute(route, hit)).toMatchObject({
      mode: "library",
      job: "job-7",
      view: "preview",
      unit: 5,
    });
    expect(hitUrl(hit)).toBe("/?mode=library&job=job-7&view=preview&unit=5");
  });
});

describe("searchSnippet", () => {
  it("collapses whitespace and leaves short text alone", () => {
    expect(searchSnippet("  a \n\n b\t c ", "x")).toBe("a b c");
  });

  it("windows long text around the first matching word with ellipses", () => {
    const text = `${"あ".repeat(200)}目的語${"い".repeat(400)}`;
    const snippet = searchSnippet(text, "ない 目的語");
    expect(snippet.startsWith("…")).toBe(true);
    expect(snippet.endsWith("…")).toBe(true);
    expect(snippet).toContain("目的語");
    expect(snippet.length).toBe(302);
  });

  it("starts at the beginning when nothing matches", () => {
    const snippet = searchSnippet("う".repeat(500), "zzz");
    expect(snippet.startsWith("…")).toBe(false);
    expect(snippet.endsWith("…")).toBe(true);
  });
});
