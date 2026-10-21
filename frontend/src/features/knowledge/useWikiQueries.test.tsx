import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { queryKeys } from "../../lib/query-keys";
import { HttpError } from "../../lib/http";
import { articleInterval, useWikiInvalidation } from "./useWikiQueries";

function setup() {
  const client = new QueryClient();
  const keys = {
    catalog: queryKeys.wikiCatalog,
    article: queryKeys.wikiArticle("a1"),
    library: queryKeys.library,
  };
  for (const key of Object.values(keys)) client.setQueryData(key, { ok: 1 });
  const stale = (key: readonly unknown[]) =>
    client.getQueryState(key)?.isInvalidated === true;
  const { result } = renderHook(() => useWikiInvalidation(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  return { result, keys, stale, client };
}

describe("useWikiInvalidation", () => {
  it("makes the catalog and every article stale after an import or a save", async () => {
    for (const run of ["import", "save"] as const) {
      const { result, keys, stale } = setup();
      if (run === "import") await result.current.afterImport();
      else result.current.afterSave();
      expect(stale(keys.catalog)).toBe(true);
      expect(stale(keys.article)).toBe(true);
      expect(stale(keys.library)).toBe(false);
    }
  });

  it("makes the catalog stale and forgets only the deleted article after a delete", async () => {
    const { result, keys, stale, client } = setup();
    const other = queryKeys.wikiArticle("a2");
    client.setQueryData(other, { ok: 2 });
    await result.current.afterDelete("a1");
    expect(stale(keys.catalog)).toBe(true);
    expect(client.getQueryData(keys.article)).toBeUndefined();
    expect(client.getQueryData(other)).toEqual({ ok: 2 });
    expect(stale(other)).toBe(false);
  });
});

describe("articleInterval", () => {
  it("watches an article, backs off after a failure, and stops when it is gone", () => {
    expect(articleInterval({})).toBe(5000);
    expect(articleInterval({ error: new HttpError(500, "x") })).toBe(10000);
    expect(articleInterval({ error: new HttpError(404, "x") })).toBe(false);
    expect(articleInterval({ error: new HttpError(410, "x") })).toBe(false);
  });
});
