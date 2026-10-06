import { afterEach, expect, spyOn, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Hono } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { createApi } from "../src/app";
import { loadConfig } from "../src/config";
import { ApiError } from "../src/contracts";
import { installLarmLifecycle } from "../src/larm-lifecycle";
import { Store } from "../src/store";

const dirs: string[] = [],
  stores: Store[] = [];
afterEach(async () => {
  for (const s of stores.splice(0)) s.db.close();
  for (const d of dirs.splice(0)) await rm(d, { recursive: true, force: true });
});
async function setup() {
  const dir = await mkdtemp(join(tmpdir(), "docling-larm-"));
  dirs.push(dir);
  const store = new Store(join(dir, "test.sqlite"));
  stores.push(store);
  const app = new Hono();
  const calls: string[] = [];
  let busy = 0,
    available = true;
  installLarmLifecycle(app, store, {
    token: "l".repeat(40),
    processor: async (method) => {
      calls.push(method);
      if (!available) throw new ApiError(503, "processor_unavailable");
      return { bootId: "processor", activeJobs: busy };
    },
  });
  app.onError((err, c) =>
    c.json(
      { error: err.message },
      (err instanceof ApiError ? err.status : 500) as ContentfulStatusCode,
    ),
  );
  app.get("/api/v1/test", (c) => c.json({ ok: true }));
  const headers = { authorization: `Bearer ${"l".repeat(40)}` };
  return {
    app,
    store,
    dir,
    calls,
    headers,
    busy: (n: number) => {
      busy = n;
    },
    offline: () => {
      available = false;
    },
  };
}
test("lifecycle requires independent token and drains/resumes without exposing document data", async () => {
  const f = await setup();
  expect((await f.app.request("/internal/larm/activity")).status).toBe(401);
  expect(
    (await f.app.request("/internal/larm/activity", { headers: { authorization: "l".repeat(40) } }))
      .status,
  ).toBe(401);
  const drain = await (
    await f.app.request("/internal/larm/drain", { method: "POST", headers: f.headers })
  ).json();
  expect(drain.draining).toBe(true);
  expect(drain.activeRequests).toBe(0);
  expect(f.calls).toContain("drain");
  expect((await f.app.request("/api/v1/test")).status).toBe(503);
  expect(
    (
      await f.app.request("/internal/larm/resume", {
        method: "POST",
        headers: f.headers,
        body: JSON.stringify({ drainToken: "wrong" }),
      })
    ).status,
  ).toBe(409);
  expect(
    (
      await f.app.request("/internal/larm/resume", {
        method: "POST",
        headers: f.headers,
        body: JSON.stringify({ drainToken: drain.drainToken }),
      })
    ).status,
  ).toBe(200);
  expect((await f.app.request("/api/v1/test")).status).toBe(200);
});
test("production API rejects unauthenticated traffic before recording application activity", async () => {
  const f = await setup();
  const { app } = createApi({
    store: f.store,
    artifactRoot: f.dir,
    clients: () => [],
    extractor: {
      validate: async () => {},
      extract: async () => {
        throw new Error("unused");
      },
    },
    larmToken: "l".repeat(40),
    processorLifecycle: async () => ({ bootId: "processor", activeJobs: 0 }),
  });
  const before = await (
    await app.request("/internal/larm/activity", { headers: f.headers })
  ).json();
  expect((await app.request("/api/v1/search", { method: "POST", body: "{}" })).status).toBe(401);
  const after = await (await app.request("/internal/larm/activity", { headers: f.headers })).json();
  expect(after.sequence).toBe(before.sequence + 1);
  expect(after.activeRequests).toBe(0);
});
test("processor lifecycle bridge bounds responses and validates its boot identity", async () => {
  const env = {
    KNOWLEDGE_WORKER_TOKEN: "w".repeat(40),
    KNOWLEDGE_LARM_TOKEN: "l".repeat(40),
    KNOWLEDGE_CLIENTS_B64: Buffer.from(
      JSON.stringify([
        {
          id: "batch",
          token: "c".repeat(40),
          issuer: "local",
          keys: {},
          scopes: [{ collection_id: "docs" }],
          actions: ["read"],
          mode: "batch",
        },
      ]),
    ).toString("base64"),
  };
  const config = loadConfig(env);
  const lifecycle = config.processorLifecycle;
  if (!lifecycle) throw new Error("lifecycle is required");
  let response: unknown = { bootId: "123e4567-e89b-42d3-a456-426614174000", activeJobs: 0 };
  const mocked = spyOn(globalThis, "fetch").mockImplementation((async (
    _url: Parameters<typeof fetch>[0],
  ) => Response.json(response)) as typeof fetch);
  try {
    expect((await lifecycle("activity")).activeJobs).toBe(0);
    response = { bootId: "unexpected boot", activeJobs: 0 };
    await expect(lifecycle("activity")).rejects.toThrow("processor_activity_unavailable");
    response = { large: "x".repeat(4097) };
    await expect(lifecycle("activity")).rejects.toThrow("processor_activity_unavailable");
    expect(() => loadConfig({ ...env, KNOWLEDGE_LARM_TOKEN: "bad token" })).toThrow(
      "KNOWLEDGE_LARM_TOKEN",
    );
  } finally {
    mocked.mockRestore();
  }
});
test("an admitted request enqueuing during processor observation cannot be reported idle", async () => {
  let queued = 0;
  const store = {
    db: { query: (sql: string) => ({ get: () => ({ n: sql.includes("'queued'") ? queued : 0 }) }) },
  } as unknown as Store;
  const app = new Hono();
  const started = Promise.withResolvers<void>(),
    finish = Promise.withResolvers<void>();
  const observing = Promise.withResolvers<void>(),
    observed = Promise.withResolvers<void>();
  installLarmLifecycle(app, store, {
    token: "l".repeat(40),
    processor: async () => {
      observing.resolve();
      await observed.promise;
      return { bootId: "processor", activeJobs: 0 };
    },
  });
  app.post("/api/v1/late", async (c) => {
    started.resolve();
    await finish.promise;
    queued++;
    return c.json({ ok: true });
  });
  const request = app.request("/api/v1/late", { method: "POST" });
  await started.promise;
  const draining = app.request("/internal/larm/drain", {
    method: "POST",
    headers: { authorization: `Bearer ${"l".repeat(40)}` },
  });
  await observing.promise;
  finish.resolve();
  await request;
  observed.resolve();
  const activity = await (await draining).json();
  expect(activity.activeRequests).toBe(0);
  expect(activity.queuedJobs).toBe(1);
});
test("resume cannot overtake a processor drain observation", async () => {
  const store = { db: { query: () => ({ get: () => ({ n: 0 }) }) } } as unknown as Store;
  const app = new Hono(),
    headers = { authorization: `Bearer ${"l".repeat(40)}` };
  let drainCount = 0,
    processorDraining = false;
  const calls: string[] = [],
    observing = Promise.withResolvers<void>(),
    finish = Promise.withResolvers<void>();
  installLarmLifecycle(app, store, {
    token: "l".repeat(40),
    processor: async (method) => {
      calls.push(method);
      if (method === "drain") {
        if (++drainCount === 2) {
          observing.resolve();
          await finish.promise;
        }
        processorDraining = true;
      }
      if (method === "resume") processorDraining = false;
      return { bootId: "processor", activeJobs: 0 };
    },
  });
  const token = (
    await (await app.request("/internal/larm/drain", { method: "POST", headers })).json()
  ).drainToken;
  const observation = app.request("/internal/larm/activity", { headers });
  await observing.promise;
  const resume = app.request("/internal/larm/resume", {
    method: "POST",
    headers,
    body: JSON.stringify({ drainToken: token }),
  });
  await Bun.sleep(1);
  expect(calls).not.toContain("resume");
  finish.resolve();
  await observation;
  expect((await (await resume).json()).draining).toBe(false);
  expect(processorDraining).toBe(false);
});
test("private resume validates and bounds its JSON body", async () => {
  const f = await setup();
  for (const [body, expected] of [
    ["{", 422],
    [JSON.stringify({ drainToken: "x", command: "resume" }), 422],
    ["x".repeat(1025), 413],
  ] as const) {
    expect(
      (await f.app.request("/internal/larm/resume", { method: "POST", headers: f.headers, body }))
        .status,
    ).toBe(expected);
  }
  expect(f.calls).not.toContain("resume");
});
test("processor activity and unavailable observations never become idle", async () => {
  const f = await setup();
  f.busy(1);
  const a = await (await f.app.request("/internal/larm/activity", { headers: f.headers })).json();
  expect(a.processorActiveJobs).toBe(1);
  f.offline();
  expect((await f.app.request("/internal/larm/activity", { headers: f.headers })).status).toBe(503);
});
test("in-flight requests remain visible while drain rejects new requests", async () => {
  const f = await setup();
  let done!: () => void;
  const gate = new Promise<void>((r) => {
    done = r;
  });
  f.app.get("/api/v1/slow", async (c) => {
    await gate;
    return c.json({ ok: true });
  });
  const request = f.app.request("/api/v1/slow");
  await Bun.sleep(1);
  const a = await (
    await f.app.request("/internal/larm/drain", { method: "POST", headers: f.headers })
  ).json();
  expect(a.activeRequests).toBe(1);
  expect(f.calls).not.toContain("drain");
  done();
  await request;
  const final = await (
    await f.app.request("/internal/larm/activity", { headers: f.headers })
  ).json();
  expect(final.activeRequests).toBe(0);
});
