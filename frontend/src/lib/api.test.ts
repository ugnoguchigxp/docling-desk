import { afterEach, expect, test, vi } from "vitest";
import { errorText, fileUrl, library, originalUrl, post, request } from "./api";
afterEach(() => vi.unstubAllGlobals());

test("reports HTTP errors even when an intermediary responds with HTML", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(new Response("<h1>Unavailable</h1>", { status: 503 })),
  );
  await expect(request("/api/library")).rejects.toThrow("HTTP 503");
});
test("preserves a JSON server error and rejects malformed successful responses", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "保存データを確認できません。" }), {
        status: 422,
      }),
    )
    .mockResolvedValueOnce(new Response("not JSON"));
  vi.stubGlobal("fetch", fetch);
  await expect(request("/api/library")).rejects.toThrow("保存データ");
  await expect(request("/api/library")).rejects.toThrow("応答を読み込めません");
});
test("decodes success, posts JSON, and builds file URLs", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ ok: 1 })))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 1 } }), { status: 400 }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ folders: [], jobs: [] })),
    )
    .mockResolvedValue(new Response(JSON.stringify({ saved: true })));
  vi.stubGlobal("fetch", fetch);
  const signal = new AbortController().signal;
  await expect(request("/api/ok", { signal }, (value) => value)).resolves.toEqual({
    ok: 1,
  });
  await expect(request("/api/bad")).rejects.toThrow("HTTP 400");
  await expect(library(signal)).resolves.toEqual({ folders: [], jobs: [] });
  await post("/api/jobs/1", { name: "a" });
  expect(fetch).toHaveBeenLastCalledWith(
    "/api/jobs/1",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "a" }),
    }),
  );
  expect(fileUrl("a/b", "dir/名前.pdf", true)).toBe(
    "/files/a%2Fb/dir/%E5%90%8D%E5%89%8D.pdf?download=true",
  );
  expect(
    originalUrl({ id: "job", filename: "fallback.PDF", original_filename: null }),
  ).toBe("/files/job/original.pdf?download=true");
  expect(originalUrl({ id: "job", filename: "plain" })).toBe(
    "/files/job/originaln?download=true",
  );
  expect(errorText(new Error("失敗"))).toBe("失敗");
  expect(errorText("text")).toBe("text");
});
