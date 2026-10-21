import { afterEach, describe, expect, it, vi } from "vitest";

// `leaving` and the root path are read once per module load.
async function load(base?: string) {
  document.head.innerHTML = base
    ? `<meta name="docling-base" content="${base}">`
    : "";
  vi.resetModules();
  return import("./http");
}
const fetchMock = vi.fn();
function stubLocation() {
  const assign = vi.fn();
  vi.stubGlobal("location", {
    assign,
    href: "https://site.example/app/?x=1",
    pathname: "/app/",
    search: "?x=1",
    hash: "",
  });
  return assign;
}
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), { status, headers });

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  document.head.innerHTML = "";
});

describe("failed responses keep what the caller needs", () => {
  it.each([
    [409, "別の下書きがあります。"],
    [412, "他の場所で更新されました。"],
    [428, "If-Match が必要です。"],
  ])("%i: status, detail, body and ETag survive", async (status, detail) => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(
      json({ detail, extra: 1 }, status, { ETag: '"current"' }),
    );
    const { requestJson, HttpError } = await load();
    const error = await requestJson("/api/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect(error).toMatchObject({
      status,
      detail,
      message: detail,
      body: { detail, extra: 1 },
    });
    expect((error as InstanceType<typeof HttpError>).headers?.get("ETag")).toBe(
      '"current"',
    );
  });

  it("joins validation issues without the pydantic prefix", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(
      json(
        { detail: [{ msg: "Value error, 名前が長すぎます。" }, { loc: [] }] },
        422,
      ),
    );
    const { requestJson } = await load();
    await expect(requestJson("/api/x")).rejects.toMatchObject({
      status: 422,
      detail: "名前が長すぎます。\n入力内容を確認してください。",
    });
  });

  it("reports a non-JSON failure by status and keeps a clipped copy of the text", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock
      .mockResolvedValueOnce(
        new Response("<h1>Bad gateway</h1>", { status: 502 }),
      )
      .mockResolvedValueOnce(new Response("x".repeat(5000), { status: 500 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));
    const { requestJson } = await load();
    await expect(requestJson("/api/x")).rejects.toMatchObject({
      status: 502,
      detail: "HTTP 502",
      body: "<h1>Bad gateway</h1>",
    });
    const long = await requestJson("/api/x").catch((e: unknown) => e);
    expect((long as { body: string }).body).toHaveLength(2000);
    await expect(requestJson("/api/x")).rejects.toMatchObject({
      status: 503,
      body: null,
    });
  });
});

describe("success bodies are read by kind", () => {
  it("never turns text or an original into JSON, and passes ETag and Content-Type on", async () => {
    vi.stubGlobal("fetch", fetchMock);
    const scene = '{"type":"excalidraw"}\n';
    fetchMock.mockResolvedValueOnce(
      new Response(scene, {
        headers: { ETag: '"r1"', "Content-Type": "application/vnd.x+json" },
      }),
    );
    const { requestText } = await load();
    expect(await requestText("/api/x")).toEqual({
      text: scene,
      etag: '"r1"',
      contentType: "application/vnd.x+json",
    });
  });

  it("accepts 204 only through the empty reader", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(null, { status: 204, headers: { ETag: '"d2"' } }),
      );
    const { requestJson, requestEmpty } = await load();
    await expect(
      requestJson("/api/x", { method: "DELETE" }),
    ).rejects.toMatchObject({
      status: 204,
      detail: "応答を読み込めませんでした。",
    });
    expect(await requestEmpty("/api/x", { method: "DELETE" })).toEqual({
      etag: '"d2"',
    });
  });

  it("rejects a broken successful body and runs the decoder on a good one", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock
      .mockResolvedValueOnce(new Response("not JSON"))
      .mockResolvedValueOnce(json({ n: 2 }));
    const { requestJson } = await load();
    await expect(requestJson("/api/x")).rejects.toMatchObject({
      status: 200,
      detail: "応答を読み込めませんでした。",
    });
    expect(
      await requestJson("/api/x", {}, (v) => (v as { n: number }).n * 2),
    ).toBe(4);
  });
});

describe("401", () => {
  it("navigates to the login once however many requests fail together", async () => {
    vi.stubGlobal("fetch", fetchMock);
    const assign = stubLocation();
    fetchMock.mockImplementation(async () =>
      json({ login_url: "/login", detail: "ログインが必要です。" }, 401),
    );
    const { requestJson } = await load();
    const results = await Promise.allSettled([
      requestJson("/api/a"),
      requestJson("/api/b"),
      requestJson("/api/c"),
    ]);
    expect(results.map((r) => r.status)).toEqual(Array(3).fill("rejected"));
    for (const r of results)
      expect((r as PromiseRejectedResult).reason).toMatchObject({
        status: 401,
        detail: "ログインが必要です。",
      });
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(
      `/login?next=${encodeURIComponent("https://site.example/app/?x=1")}`,
    );
  });

  it("does not navigate for a 401 without a login address, or for other statuses", async () => {
    vi.stubGlobal("fetch", fetchMock);
    const assign = stubLocation();
    fetchMock
      .mockResolvedValueOnce(json({ detail: "x" }, 401))
      .mockResolvedValueOnce(json({ login_url: "/login" }, 403));
    const { requestJson } = await load();
    await expect(requestJson("/api/a")).rejects.toMatchObject({ status: 401 });
    await expect(requestJson("/api/b")).rejects.toMatchObject({ status: 403 });
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("cancellation and communication failure", () => {
  it("keeps an abort as an abort, before and during the body read", async () => {
    vi.stubGlobal("fetch", fetchMock);
    const { requestJson, requestText, HttpError, isAbort } = await load();
    fetchMock.mockRejectedValueOnce(new DOMException("x", "AbortError"));
    const before = await requestJson("/api/x").catch((e: unknown) => e);
    expect(before).not.toBeInstanceOf(HttpError);
    expect(isAbort(before)).toBe(true);

    const controller = new AbortController();
    fetchMock.mockImplementationOnce(
      async (_url: string, init: RequestInit) =>
        new Response(
          new ReadableStream({
            start(stream) {
              init.signal?.addEventListener("abort", () =>
                stream.error(new DOMException("x", "AbortError")),
              );
            },
          }),
        ),
    );
    const pending = requestText("/api/x", { signal: controller.signal }).catch(
      (e: unknown) => e,
    );
    controller.abort();
    const during = await pending;
    expect(during).not.toBeInstanceOf(HttpError);
    expect(isAbort(during)).toBe(true);
  });

  it("an error raised after the caller aborted is not reported as a lost connection", async () => {
    vi.stubGlobal("fetch", fetchMock);
    const { requestJson, HttpError } = await load();
    const controller = new AbortController();
    controller.abort();
    const reset = new TypeError("terminated");
    fetchMock.mockRejectedValueOnce(reset);
    const error = await requestJson("/api/x", {
      signal: controller.signal,
    }).catch((e: unknown) => e);
    expect(error).toBe(reset);
    expect(error).not.toBeInstanceOf(HttpError);
  });

  it("reports a missing response as status 0 and never repeats the request", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { requestJson } = await load();
    await expect(
      requestJson("/api/x", { method: "POST", body: "{}" }),
    ).rejects.toMatchObject({ status: 0 });
    await expect(requestJson("/api/x")).rejects.toMatchObject({ status: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry a failed response either", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValue(json({ detail: "busy" }, 503));
    const { requestJson } = await load();
    await expect(
      requestJson("/api/x", { method: "POST", body: "{}" }),
    ).rejects.toMatchObject({ status: 503 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("request options and root path", () => {
  it("prefixes root-relative URLs only and lets the caller keep its options", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation(async () => json({}));
    const { requestJson } = await load("/assessment/");
    const signal = new AbortController().signal;
    await requestJson("/api/x", {
      method: "PUT",
      headers: { "If-Match": '"a"' },
      body: "b",
      signal,
    });
    await requestJson("https://other.example/api/x");
    await requestJson("/assessment/api/y", { cache: "default" });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/assessment/api/x",
      "https://other.example/api/x",
      "/assessment/api/y",
    ]);
    expect(fetchMock.mock.calls[0][1]).toEqual({
      cache: "no-store",
      method: "PUT",
      headers: { "If-Match": '"a"' },
      body: "b",
      signal,
    });
    expect(fetchMock.mock.calls[2][1]).toEqual({ cache: "default" });
  });

  it("withDetail rewords a failure without losing status, body or headers", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(
      json({ detail: "x" }, 404, { ETag: '"e"' }),
    );
    const { requestJson, withDetail } = await load();
    const original = await requestJson("/api/x").catch((e: unknown) => e);
    const reworded = withDetail(original as never, "読み込めませんでした。");
    expect(reworded).toMatchObject({
      status: 404,
      message: "読み込めませんでした。",
      body: { detail: "x" },
    });
    expect(reworded.headers?.get("ETag")).toBe('"e"');
  });
});
