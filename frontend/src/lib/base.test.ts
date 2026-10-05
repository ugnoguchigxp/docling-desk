import { afterEach, describe, expect, it, vi } from "vitest";

async function load(base?: string) {
  document.head.innerHTML = base
    ? `<meta name="docling-base" content="${base}">`
    : "";
  vi.resetModules();
  return import("./base");
}

describe("withBase", () => {
  afterEach(() => {
    document.head.innerHTML = "";
  });

  it("leaves paths alone without a prefix", async () => {
    const { withBase, basePath } = await load();
    expect(basePath).toBe("");
    expect(withBase("/api/library")).toBe("/api/library");
  });

  it("prefixes root-relative paths once", async () => {
    const { withBase } = await load("/assessment/");
    expect(withBase("/api/library")).toBe("/assessment/api/library");
    expect(withBase("/assessment/api/library")).toBe("/assessment/api/library");
    expect(withBase("/")).toBe("/assessment/");
  });

  it("ignores external, protocol-relative and relative URLs", async () => {
    const { withBase } = await load("/assessment");
    expect(withBase("https://example.com/api")).toBe("https://example.com/api");
    expect(withBase("//example.com/api")).toBe("//example.com/api");
    expect(withBase("about:blank")).toBe("about:blank");
  });

  it("rejects an unsafe prefix", async () => {
    const { basePath } = await load("//evil.example");
    expect(basePath).toBe("");
  });
});
