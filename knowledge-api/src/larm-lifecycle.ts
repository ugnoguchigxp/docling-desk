import { randomUUID, timingSafeEqual } from "node:crypto";
import type { Env, Hono } from "hono";
import { z } from "zod";
import { ApiError } from "./contracts";
import type { Store } from "./store";

/** Runtime-only counters; document identities and content never leave this adapter. */
export function installLarmLifecycle<E extends Env>(
  app: Hono<E>,
  store: Store,
  options: {
    token?: string;
    processor: (
      method: "activity" | "drain" | "resume",
    ) => Promise<{ bootId: string; activeJobs: number }>;
  },
) {
  const bootId = randomUUID();
  let sequence = 0,
    activeRequests = 0,
    drainToken: string | null = null;
  let chain: Promise<void> = Promise.resolve();
  const serialized = <T>(work: () => Promise<T>): Promise<T> => {
    const task = chain.then(work);
    chain = task.then(
      () => undefined,
      () => undefined,
    );
    return task;
  };
  const counters = () => ({
    queuedJobs: store.db
      .query<{ n: number }, []>("SELECT count(*) n FROM jobs WHERE state='queued'")
      .get()?.n,
    runningJobs: store.db
      .query<{ n: number }, []>("SELECT count(*) n FROM jobs WHERE state='running'")
      .get()?.n,
    activeRequests,
  });
  const activity = async () => {
    const before = counters();
    if (before.queuedJobs === undefined || before.runningJobs === undefined)
      throw new ApiError(503, "activity_unavailable");
    const p = await options.processor(
      drainToken && !before.queuedJobs && !before.runningJobs && !before.activeRequests
        ? "drain"
        : "activity",
    );
    // A request admitted before drain may enqueue a job while the processor
    // observation is in flight. Never combine old queue counts with new counters.
    const current = counters();
    if (current.queuedJobs === undefined || current.runningJobs === undefined)
      throw new ApiError(503, "activity_unavailable");
    return {
      contractVersion: "larm.local-service-activity.v1",
      bootId: `${bootId}.${p.bootId}`,
      sequence: ++sequence,
      observedAt: new Date().toISOString(),
      ...current,
      processorActiveJobs: p.activeJobs,
      draining: drainToken !== null,
      drainToken,
    };
  };
  app.use("/api/v1/*", async (_c, next) => {
    if (drainToken) throw new ApiError(503, "service_draining", "service draining", true);
    activeRequests++;
    sequence++;
    try {
      await next();
    } finally {
      activeRequests--;
      sequence++;
    }
  });
  app.use("/internal/larm/*", async (c, next) => {
    const supplied = /^Bearer (\S+)$/i.exec(c.req.header("authorization") ?? "")?.[1] ?? "";
    if (
      !options.token ||
      options.token.length < 32 ||
      Buffer.byteLength(supplied) !== Buffer.byteLength(options.token) ||
      !timingSafeEqual(Buffer.from(supplied), Buffer.from(options.token))
    )
      throw new ApiError(401, "unauthenticated");
    if (c.req.header("origin")) throw new ApiError(403, "server_to_server_only");
    await next();
  });
  app.get("/internal/larm/activity", async (c) => c.json(await serialized(activity)));
  app.post("/internal/larm/drain", async (c) =>
    c.json(
      await serialized(async () => {
        drainToken ??= randomUUID();
        sequence++;
        return activity();
      }),
    ),
  );
  app.post("/internal/larm/resume", async (c) => {
    const body = await resumeBody(c.req.raw);
    return c.json(
      await serialized(async () => {
        if (!drainToken || body.drainToken !== drainToken) throw new ApiError(409, "drain_changed");
        await options.processor("resume");
        drainToken = null;
        sequence++;
        return activity();
      }),
    );
  });
}

async function resumeBody(request: Request): Promise<{ drainToken: string }> {
  const reader = request.body?.getReader();
  if (!reader) throw new ApiError(422, "invalid_json");
  let bytes = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const v = await reader.read();
      if (v.done) break;
      bytes += v.value.byteLength;
      if (bytes > 1024) throw new ApiError(413, "request_too_large");
      chunks.push(v.value);
    }
    const parsed = z
      .object({ drainToken: z.string().min(1).max(128) })
      .strict()
      .safeParse(JSON.parse(Buffer.concat(chunks).toString("utf8")));
    if (!parsed.success) throw new ApiError(422, "invalid_json");
    return parsed.data;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(422, "invalid_json");
  } finally {
    await reader.cancel().catch(() => {});
  }
}
