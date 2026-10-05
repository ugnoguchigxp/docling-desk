import { afterEach, describe, expect, test } from "bun:test";
import { createHmac, randomUUID } from "node:crypto";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { z } from "zod";
import { createApi } from "../src/app";
import { type Client, clientSchema } from "../src/auth";
import { ApiError, type Extraction, MAX_FILE, MAX_JSON, type Reference } from "../src/contracts";
import {
  type AnswerProvider,
  type EmbeddingProvider,
  type Extractor,
  GatewayEmbedding,
} from "../src/providers";
import { errorResponse, responseSchemas, type searchResponse } from "../src/responses";
import type { JobRow, RevisionRow } from "../src/store";
import { Store } from "../src/store";

const client = clientSchema.parse({
  id: "main",
  token: "t".repeat(40),
  issuer: "main",
  keys: { v1: "k".repeat(40) },
  mode: "actor",
  actions: ["read", "write", "answer"],
  scopes: [
    { collection_id: "wiki" },
    { collection_id: "assessment", project_id: "p1", region: "JP" },
  ],
});
const other = clientSchema.parse({
  ...client,
  id: "other",
  token: "o".repeat(40),
  issuer: "other",
  keys: { v1: "q".repeat(40) },
  scopes: [{ collection_id: "assessment", project_id: "p2", region: "US" }],
});
export function headers(
  c = client,
  claims: Record<string, unknown> = {},
  header: Record<string, unknown> = {},
) {
  const time = Math.floor(Date.now() / 1000);
  const h = Buffer.from(
    JSON.stringify({ alg: "HS256", typ: "JWT", kid: "v1", ...header }),
  ).toString("base64url");
  const p = Buffer.from(
    JSON.stringify({
      iss: c.issuer,
      aud: "docling-desk-api",
      sub: "user-1",
      client_id: c.id,
      iat: time,
      exp: time + 300,
      scopes: c.scopes,
      ...claims,
    }),
  ).toString("base64url");
  const signature = createHmac("sha256", c.keys.v1 ?? "")
    .update(`${h}.${p}`)
    .digest("base64url");
  return { authorization: `Bearer ${c.token}`, "x-knowledge-actor": `${h}.${p}.${signature}` };
}
class FakeExtractor implements Extractor {
  calls = 0;
  viewerReady = false;
  viewer?: Extractor["viewer"];
  pause: (() => Promise<void>) | undefined;
  constructor(private root: string) {}
  async validate(input: {
    source_id: string;
    source_revision: string;
    suffix: string;
    sha256: string;
  }) {
    const data = await readFile(
      join(this.root, "sources", input.source_id, input.source_revision, `original${input.suffix}`),
    );
    if (Bun.CryptoHasher.hash("sha256", data, "hex") !== input.sha256)
      throw new ApiError(422, "invalid_document");
  }
  async extract(job: JobRow, revision: RevisionRow): Promise<Extraction> {
    this.calls++;
    const text = await readFile(
      join(this.root, "sources", revision.source_id, revision.id, `original${revision.suffix}`),
      "utf8",
    );
    await this.pause?.();
    const contexts = text.split("\n\n").map((paragraph, i) => ({
      id: `ctx${i}`,
      text: paragraph,
      parent_id: null,
      source_sha256: revision.sha256,
      kind: "section",
      headings: ["Notes"],
      refs: [],
      context_refs: [],
      pages: [],
      provenance: [],
      relations: [],
      row_range: [],
      tables: [],
    }));
    return {
      source_id: revision.source_id,
      source_revision: revision.id,
      job_id: job.id,
      run: job.run,
      source_sha256: revision.sha256,
      profile: "local-v1",
      contexts,
      chunks: contexts.map((c, i) => ({ ...c, id: `chunk${i}`, parent_id: c.id })),
      warnings: [],
      viewer_ready: this.viewerReady,
    };
  }
}
class FakeEmbedding implements EmbeddingProvider {
  identity = "test-model-v1";
  dimensions = 2;
  calls = 0;
  async embed(texts: string[]) {
    this.calls++;
    return texts.map((t) => (/在庫|stock/i.test(t) ? [1, 0] : [0, 1]));
  }
}
const fixtures: { root: string; store: Store }[] = [];
async function fixture(
  options: {
    embedding?: EmbeddingProvider;
    answer?: AnswerProvider;
    persistent?: boolean;
    searchGate?: () => Promise<void>;
  } = {},
) {
  const root = await mkdtemp(join(tmpdir(), "knowledge-api-test-"));
  const store = new Store(options.persistent ? join(root, "db.sqlite") : ":memory:");
  fixtures.push({ root, store });
  const extractor = new FakeExtractor(root);
  let clients: Client[] = [client, other];
  const api = createApi({
    store,
    artifactRoot: root,
    clients: () => clients,
    extractor,
    ...options,
  });
  const req = async (
    path: string,
    method = "GET",
    body?: unknown,
    c = client,
    extra: Record<string, string> = {},
  ) => {
    const response = await api.app.request(path, {
      method,
      headers: { ...headers(c), ...(body ? { "content-type": "application/json" } : {}), ...extra },
      body: body ? JSON.stringify(body) : undefined,
    });
    await checkContract(response, method, path);
    return response;
  };
  async function upload(
    text = "在庫連携の確認が必要です。",
    m: Record<string, unknown> = {},
    path = "/api/v1/sources",
    extra: Record<string, string> = {},
    c = client,
  ) {
    const form = new FormData();
    form.append("file", new File([text], "notes.txt"));
    form.append("metadata", JSON.stringify({ title: "Notes", collection_id: "wiki", ...m }));
    const response = await api.app.request(path, {
      method: "POST",
      headers: { ...headers(c), "idempotency-key": randomUUID(), ...extra },
      body: form,
    });
    await checkContract(response, "POST", path);
    return response;
  }
  async function ready(text?: string, m?: Record<string, unknown>) {
    const response = await upload(text, m);
    expect(response.status).toBe(202);
    const result = (await response.json()) as {
      source_id: string;
      source_revision: string;
      job_id: string;
    };
    await api.worker.tick();
    return result;
  }
  const search = (query = "在庫", mode = "text", scope: unknown = { collection_ids: ["wiki"] }) =>
    req("/api/v1/search", "POST", { query, mode, scope });
  return {
    ...api,
    root,
    extractor,
    req,
    upload,
    ready,
    search,
    setClients: (value: Client[]) => {
      clients = value;
    },
  };
}
async function checkContract(response: Response, method: string, path: string) {
  if (!response.headers.get("content-type")?.includes("application/json")) return;
  const route = (path.split("?")[0] ?? path)
    .replace(/\/sources\/[^/]+/, "/sources/{source_id}")
    .replace(/\/jobs\/[^/]+/, "/jobs/{job_id}")
    .replace(/\/answers\/[^/]+/, "/answers/{answer_id}");
  const schema =
    response.status >= 400 ? errorResponse : responseSchemas[`${method.toLowerCase()} ${route}`];
  schema?.parse(await response.clone().json());
}
afterEach(async () => {
  delete process.env.DOCLING_QUALITY_FAULT;
  for (const f of fixtures.splice(0)) {
    f.store.db.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
type Search = z.infer<typeof searchResponse>;
const body = async (r: Response) => (await r.json()) as Search;

describe("authentication and permissions", () => {
  test("service token and signed actor are both required; bad signatures, alg and expiry fail", async () => {
    const f = await fixture();
    expect((await f.app.request("/api/v1/search", { method: "POST" })).status).toBe(401);
    expect(
      (
        await f.app.request("/api/v1/search", {
          method: "POST",
          headers: { authorization: `Bearer ${client.token}` },
        })
      ).status,
    ).toBe(401);
    for (const [claims, h] of [
      [{ exp: 1 }, {}],
      [{ aud: "wrong" }, {}],
      [{ scopes: [{ collection_id: "secret" }] }, {}],
      [{}, { alg: "none" }],
      [{}, { kid: "revoked" }],
    ]) {
      expect(
        (
          await f.app.request("/api/v1/search", {
            method: "POST",
            headers: headers(client, claims, h),
          })
        ).status,
      ).toBe(401);
    }
  });
  test("batch credentials have explicitly limited grants", async () => {
    const f = await fixture();
    f.setClients([{ ...client, mode: "batch", scopes: [{ collection_id: "wiki" }] }]);
    expect(
      (
        await f.app.request("/api/v1/search", {
          method: "POST",
          headers: { authorization: `Bearer ${client.token}`, "content-type": "application/json" },
          body: JSON.stringify({ query: "x", scope: { collection_ids: ["wiki"] }, mode: "text" }),
        })
      ).status,
    ).toBe(200);
  });
  test("missing scope, another project and browser origins cannot broaden access", async () => {
    const f = await fixture();
    expect(
      (await f.req("/api/v1/search", "POST", { query: "x", scope: { collection_ids: [] } })).status,
    ).toBe(422);
    expect((await f.search("x", "text", { collection_ids: ["assessment"] })).status).toBe(422);
    expect(
      (
        await f.search("x", "text", {
          collection_ids: ["assessment"],
          project_id: "p2",
          region: "US",
        })
      ).status,
    ).toBe(403);
    expect(
      (
        await f.req(
          "/api/v1/search",
          "POST",
          { query: "x", scope: { collection_ids: ["wiki"] } },
          client,
          { origin: "https://evil.invalid" },
        )
      ).status,
    ).toBe(403);
  });
  test("direct source, content and job IDs are hidden from unauthorized clients", async () => {
    const f = await fixture();
    const s = await f.ready();
    for (const path of [
      `/api/v1/sources/${s.source_id}`,
      `/api/v1/sources/${s.source_id}/content`,
      `/api/v1/jobs/${s.job_id}`,
    ])
      expect((await f.req(path, "GET", undefined, other)).status).toBe(404);
  });
});
describe("ingestion and retrieval", () => {
  test("Japanese short words, FTS queries and exact parent context with no invented page", async () => {
    const f = await fixture();
    await f.ready("在庫を確認します。\n\nAPI-123 の接続仕様です。");
    const s = await body(await f.search("在庫"));
    expect(s.results).toHaveLength(1);
    expect((await body(await f.search("API-123"))).results).toHaveLength(1);
    const ref = s.results[0];
    expect(ref).toBeDefined();
    const response = await f.req("/api/v1/context", "POST", {
      retrieval_id: s.retrieval_id,
      references: [pick(ref)],
      max_chars: 1000,
    });
    expect(response.status).toBe(200);
    const value = (await response.json()) as {
      contexts: { text: string; locator: { kind: string }; evidence_id: string }[];
    };
    expect(value.contexts[0]?.text).toBe("在庫を確認します。");
    expect(value.contexts[0]?.locator.kind).toBe("section");
    expect(value.contexts[0]?.evidence_id).toBe(ref?.context_id);
  });
  test("queued state and no-match are different; metadata, original content and ETag work", async () => {
    const f = await fixture();
    const response = await f.upload();
    const s = (await response.json()) as { source_id: string };
    expect((await f.search()).status).toBe(503);
    await f.worker.tick();
    const noMatch = await body(await f.search("存在しないキーワード"));
    expect(noMatch.results).toHaveLength(0);
    expect(noMatch.status).toBe("complete");
    const info = await f.req(`/api/v1/sources/${s.source_id}`);
    expect(info.headers.get("etag")).toContain(s.source_id);
    const content = await f.req(`/api/v1/sources/${s.source_id}/content`);
    expect(content.status).toBe(200);
    expect(await content.text()).toContain("在庫");
    expect(content.headers.get("x-request-id")).toBeTruthy();
    expect(content.headers.get("cache-control")).toBe("no-store");
  });
  test("semantic and hybrid really find words absent from FTS, and disabled provider is explicit", async () => {
    const embedding = new FakeEmbedding();
    const f = await fixture({ embedding });
    await f.ready();
    expect((await body(await f.search("stock", "text"))).results).toHaveLength(0);
    const result = await body(await f.search("stock", "semantic"));
    expect(result.results).toHaveLength(1);
    expect(result.effective_mode).toBe("semantic");
    const disabled = await fixture();
    await disabled.ready();
    const fallback = await body(await disabled.search("在庫", "hybrid"));
    expect(fallback.effective_mode).toBe("text");
    expect(fallback.degraded_reasons).toContain("embedding_not_configured");
  });
  test("embedding failure preserves FTS and reports failure, not semantic success", async () => {
    const provider = new FakeEmbedding();
    provider.embed = async () => {
      throw new Error("down");
    };
    const f = await fixture({ embedding: provider });
    const s = await f.ready();
    expect(f.store.job(s.job_id)?.state).toBe("failed");
    const result = await body(await f.search("在庫", "hybrid"));
    expect(result.results).toHaveLength(1);
    expect(result.effective_mode).toBe("text");
  });
  test("scope filters precede top-k and pending counts never expose another region", async () => {
    const f = await fixture({ embedding: new FakeEmbedding() });
    await f.ready("在庫を確認します。", {
      collection_id: "assessment",
      project_id: "p1",
      region: "JP",
    });
    const foreign = await f.upload(
      "stock belongs only to the US project",
      {
        collection_id: "assessment",
        project_id: "p2",
        region: "US",
      },
      undefined,
      {},
      other,
    );
    expect(foreign.status).toBe(202);
    // A pending foreign source is excluded from both results and index counts.
    const scopedPending = await body(
      await f.search("stock", "semantic", {
        collection_ids: ["assessment"],
        project_id: "p1",
        region: "JP",
      }),
    );
    expect(scopedPending.index_state.pending_sources).toBe(0);
    await f.worker.tick();
    const results = await body(
      await f.search("stock", "semantic", {
        collection_ids: ["assessment"],
        project_id: "p1",
        region: "JP",
      }),
    );
    expect(results.results).toHaveLength(1);
    const response = await f.req(
      "/api/v1/search",
      "POST",
      {
        query: "stock",
        mode: "semantic",
        scope: { collection_ids: ["assessment"], project_id: "p2", region: "US" },
      },
      other,
    );
    const us = await body(response);
    expect(us.results).toHaveLength(1);
    expect(us.results[0]?.source_id).not.toBe(results.results[0]?.source_id);
  });
  test("idempotent uploads return the same IDs; changed content conflicts", async () => {
    const f = await fixture();
    const key = randomUUID();
    const a = await f.upload("hello", {}, undefined, { "idempotency-key": key });
    const b = await f.upload("hello", {}, undefined, { "idempotency-key": key });
    expect(a.status).toBe(202);
    expect(b.status).toBe(202);
    const av = (await a.json()) as { source_id: string };
    const bv = (await b.json()) as { source_id: string };
    expect(av.source_id).toBe(bv.source_id);
    expect((await f.upload("changed", {}, undefined, { "idempotency-key": key })).status).toBe(409);
  });
  test("context response limits do not silently truncate and snapshots are user bound", async () => {
    const f = await fixture();
    await f.ready();
    const s = await body(await f.search());
    expect(
      (
        await f.req("/api/v1/context", "POST", {
          retrieval_id: s.retrieval_id,
          references: s.results.map(pick),
          max_chars: 1,
        })
      ).status,
    ).toBe(413);
    const response = await f.app.request("/api/v1/context", {
      method: "POST",
      headers: { ...headers(client, { sub: "another-user" }), "content-type": "application/json" },
      body: JSON.stringify({ retrieval_id: s.retrieval_id, references: s.results.map(pick) }),
    });
    expect(response.status).toBe(404);
    expect(
      (
        await f.req("/api/v1/context", "POST", {
          retrieval_id: s.retrieval_id,
          references: [{ ...pick(s.results[0]), context_id: randomUUID() }],
        })
      ).status,
    ).toBe(404);
  });
});
function pick(v: Reference | undefined): Reference {
  if (!v) throw new Error("Missing fixture reference");
  return {
    source_id: v.source_id,
    source_revision: v.source_revision,
    evidence_revision: v.evidence_revision,
    context_id: v.context_id,
  };
}
describe("update, deletion and recovery", () => {
  test("update removes old default results immediately, rejects stale contexts and supports explicit history", async () => {
    const f = await fixture();
    const s = await f.ready();
    const before = await body(await f.search());
    const etag = (await f.req(`/api/v1/sources/${s.source_id}`)).headers.get("etag") ?? "";
    const updated = await f.upload("請求書の確認", {}, `/api/v1/sources/${s.source_id}/revisions`, {
      "if-match": etag,
    });
    expect(updated.status).toBe(202);
    expect((await f.search()).status).toBe(503);
    expect(
      (
        await f.req("/api/v1/context", "POST", {
          retrieval_id: before.retrieval_id,
          references: before.results.map(pick),
        })
      ).status,
    ).toBe(409);
    expect(
      (
        await f.upload("stale", {}, `/api/v1/sources/${s.source_id}/revisions`, {
          "if-match": etag,
        })
      ).status,
    ).toBe(412);
    await f.worker.tick();
    expect((await body(await f.search())).results).toHaveLength(0);
    const history = await body(
      await f.req("/api/v1/search", "POST", {
        query: "在庫",
        mode: "text",
        scope: { collection_ids: ["wiki"] },
        filter: { include_past_revisions: true },
      }),
    );
    expect(history.results[0]?.is_current).toBe(false);
    const historyUrl = history.results[0]?.source_url;
    expect(historyUrl).toContain("include_past_revisions=true");
    if (!historyUrl) throw new Error("Missing history URL");
    expect((await f.req(historyUrl)).status).toBe(200);
    expect(
      (
        await f.req("/api/v1/context", "POST", {
          retrieval_id: history.retrieval_id,
          references: history.results.map(pick),
        })
      ).status,
    ).toBe(200);
  });
  test("revision replay succeeds with the original If-Match after registration", async () => {
    const f = await fixture();
    const s = await f.ready();
    const etag = (await f.req(`/api/v1/sources/${s.source_id}`)).headers.get("etag") ?? "";
    const key = randomUUID();
    const path = `/api/v1/sources/${s.source_id}/revisions`;
    const a = await f.upload("new", {}, path, { "if-match": etag, "idempotency-key": key });
    const b = await f.upload("new", {}, path, { "if-match": etag, "idempotency-key": key });
    expect(a.status).toBe(202);
    expect(b.status).toBe(202);
  });
  test("deletion invalidates every read immediately and then removes all versions", async () => {
    const f = await fixture();
    const s = await f.ready();
    const result = await body(await f.search());
    const etag = (await f.req(`/api/v1/sources/${s.source_id}`)).headers.get("etag") ?? "";
    const deleted = await f.req(`/api/v1/sources/${s.source_id}`, "DELETE", undefined, client, {
      "if-match": etag,
    });
    expect(deleted.status).toBe(202);
    expect((await f.req(`/api/v1/sources/${s.source_id}/content`)).status).toBe(404);
    expect(
      (
        await f.req("/api/v1/context", "POST", {
          retrieval_id: result.retrieval_id,
          references: result.results.map(pick),
        })
      ).status,
    ).toBe(404);
    expect((await body(await f.search())).results).toHaveLength(0);
    await f.worker.tick();
    expect(f.store.revision(s.source_revision)).toBeNull();
    const job = (await deleted.json()) as { job_id: string };
    expect((await f.req(`/api/v1/jobs/${job.job_id}`)).status).toBe(200);
  });
  test("late extraction cannot resurrect a deleted source", async () => {
    const f = await fixture();
    const response = await f.upload();
    const s = (await response.json()) as { source_id: string };
    let release = () => {};
    const wait = new Promise<void>((r) => {
      release = r;
    });
    f.extractor.pause = () => wait;
    const tick = f.worker.tick();
    while (!f.extractor.calls) await Bun.sleep(1);
    const etag = (await f.req(`/api/v1/sources/${s.source_id}`)).headers.get("etag") ?? "";
    expect(
      (
        await f.req(`/api/v1/sources/${s.source_id}`, "DELETE", undefined, client, {
          "if-match": etag,
        })
      ).status,
    ).toBe(202);
    release();
    await tick;
    await f.worker.tick();
    expect((await body(await f.search())).results).toHaveLength(0);
    expect(f.store.db.query("SELECT id FROM chunks").all()).toHaveLength(0);
  });
  test("lease fencing rejects old run results; expired local jobs resume after restart", async () => {
    const f = await fixture({ persistent: true });
    const response = await f.upload();
    const s = (await response.json()) as { job_id: string };
    const old = f.store.claim();
    expect(old?.id).toBe(s.job_id);
    f.store.db.run("UPDATE jobs SET lease_until=0 WHERE id=?", [s.job_id]);
    const fresh = f.store.claim();
    expect(fresh?.run).toBe((old?.run ?? 0) + 1);
    expect(old && f.store.live(old)).toBe(false);
    f.store.db.run("UPDATE jobs SET lease_until=0 WHERE id=?", [s.job_id]);
    await f.worker.tick();
    expect(f.store.job(s.job_id)?.state).toBe("completed");
    const reopened = new Store(join(f.root, "db.sqlite"));
    expect(reopened.job(s.job_id)?.state).toBe("completed");
    reopened.db.close();
  });
  test("reindex reuses unchanged embeddings without another extraction", async () => {
    const embedding = new FakeEmbedding();
    const f = await fixture({ embedding });
    const s = await f.ready();
    const calls = embedding.calls,
      extracts = f.extractor.calls;
    const etag = (await f.req(`/api/v1/sources/${s.source_id}`)).headers.get("etag") ?? "";
    expect(
      (
        await f.req(`/api/v1/sources/${s.source_id}/reindex`, "POST", undefined, client, {
          "if-match": etag,
        })
      ).status,
    ).toBe(202);
    await f.worker.tick();
    expect(embedding.calls).toBe(calls);
    expect(f.extractor.calls).toBe(extracts);
  });
  test("key removal and changed grants revoke access to a saved retrieval", async () => {
    const f = await fixture();
    await f.ready();
    const result = await body(await f.search());
    f.setClients([
      { ...client, scopes: [{ collection_id: "assessment", project_id: "p1", region: "JP" }] },
    ]);
    expect(
      (
        await f.req("/api/v1/context", "POST", {
          retrieval_id: result.retrieval_id,
          references: result.results.map(pick),
        })
      ).status,
    ).toBe(401);
    f.setClients([{ ...client, keys: { v2: "r".repeat(40) } }]);
    expect((await f.search()).status).toBe(401);
  });
});
describe("answers", () => {
  const answer: AnswerProvider = {
    async generate(_q, e) {
      return {
        answer: "在庫確認が必要です。",
        citation_ids: [e[0]?.evidence_id ?? ""],
        unknowns: [],
      };
    },
  };
  test("async answer stores verified citations and deletion purges the cached text", async () => {
    const f = await fixture({ answer });
    const s = await f.ready();
    const response = await f.req("/api/v1/answers", "POST", {
      query: "在庫",
      scope: { collection_ids: ["wiki"] },
    });
    expect(response.status).toBe(202);
    const saved = (await response.json()) as { answer_id: string };
    await f.worker.tick();
    const result = await f.req(`/api/v1/answers/${saved.answer_id}`);
    const value = (await result.json()) as { state: string; result: { answer: string } };
    expect(value.state).toBe("completed");
    expect(value.result.answer).toContain("在庫");
    const etag = (await f.req(`/api/v1/sources/${s.source_id}`)).headers.get("etag") ?? "";
    await f.req(`/api/v1/sources/${s.source_id}`, "DELETE", undefined, client, {
      "if-match": etag,
    });
    const invalid = await f.req(`/api/v1/answers/${saved.answer_id}`);
    expect(((await invalid.json()) as { result: unknown }).result).toBeNull();
  });
  test("no evidence, unconfigured provider, and invented citations are distinct", async () => {
    const disabled = await fixture();
    expect(
      (
        await disabled.req("/api/v1/answers", "POST", {
          query: "x",
          scope: { collection_ids: ["wiki"] },
        })
      ).status,
    ).toBe(503);
    const f = await fixture({ answer });
    const response = await f.req("/api/v1/answers", "POST", {
      query: "empty",
      scope: { collection_ids: ["wiki"] },
    });
    const queued = (await response.json()) as { answer_id: string };
    await f.worker.tick();
    expect(
      ((await (await f.req(`/api/v1/answers/${queued.answer_id}`)).json()) as { state: string })
        .state,
    ).toBe("insufficient_evidence");
    const bad = await fixture({
      answer: {
        async generate() {
          return { answer: "bad", citation_ids: ["invented"], unknowns: [] };
        },
      },
    });
    await bad.ready();
    const r = await bad.req("/api/v1/answers", "POST", {
      query: "在庫",
      scope: { collection_ids: ["wiki"] },
    });
    const a = (await r.json()) as { answer_id: string };
    await bad.worker.tick();
    expect(bad.store.job(a.answer_id)?.error_code).toBe("invalid_citations");
  });
});

test("only one API process can own a database and revoked leases cannot mutate it", async () => {
  const f = await fixture({ persistent: true });
  const second = new Store(join(f.root, "db.sqlite"));
  try {
    f.store.acquireLease();
    expect(() => second.acquireLease()).toThrow();
    f.store.db.run("UPDATE api_lease SET expires=0");
    second.acquireLease();
    expect(() => f.store.assertLeader()).toThrow("api_lease_lost");
    second.assertLeader();
    second.releaseLease();
  } finally {
    second.db.close();
  }
});
test("index readiness counts respect requested language and source-kind filters", async () => {
  const f = await fixture();
  await f.ready("ready text", { source_kind: "wiki", language: "ja" });
  await f.upload("pending English", { source_kind: "document", language: "en" });
  const response = await f.req("/api/v1/search", "POST", {
    query: "ready",
    mode: "text",
    scope: { collection_ids: ["wiki"] },
    filter: { source_kinds: ["wiki"], languages: ["ja"] },
  });
  expect(response.status).toBe(200);
  expect((await body(response)).index_state.pending_sources).toBe(0);
});

describe("review regressions", () => {
  test("parallel requests reserve search slots before reading request bodies", async () => {
    const provider = new FakeEmbedding();
    const f = await fixture({ embedding: provider });
    await f.ready();
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const embed = provider.embed.bind(provider);
    provider.embed = async (texts) => {
      await gate;
      return embed(texts);
    };
    const requests = Array.from({ length: 9 }, () => f.search("stock", "semantic"));
    await Bun.sleep(20);
    release();
    const responses = await Promise.all(requests);
    expect(responses.filter((r) => r.status === 200)).toHaveLength(8);
    expect(responses.filter((r) => r.status === 429)).toHaveLength(1);
    expect((await f.search("stock", "semantic")).status).toBe(200);
  });
  test("identical text in distinct sources retains each citation and parent context", async () => {
    const f = await fixture();
    const first = await f.ready("同じ在庫確認です", { title: "First" });
    const second = await f.ready("同じ在庫確認です", { title: "Second" });
    const result = await body(await f.search());
    expect(result.results).toHaveLength(2);
    expect(new Set(result.results.map((r) => r.source_id))).toEqual(
      new Set([first.source_id, second.source_id]),
    );
    const response = await f.req("/api/v1/context", "POST", {
      retrieval_id: result.retrieval_id,
      references: result.results.map(pick),
    });
    expect(((await response.json()) as { contexts: unknown[] }).contexts).toHaveLength(2);
  });
  test("short text queries normalize width and Unicode case consistently", async () => {
    const f = await fixture();
    await f.ready("ＡＩとΛΓの連携を確認します");
    for (const query of ["AI", "ＡＩ", "λγ"]) {
      expect((await body(await f.search(query))).results).toHaveLength(1);
    }
  });
});

describe("review ingestion and provider boundaries", () => {
  test("a full extraction queue still allows an idempotent replay", async () => {
    const f = await fixture();
    const key = randomUUID();
    const saved = await f.upload("replay", {}, undefined, { "idempotency-key": key });
    expect(saved.status).toBe(202);
    await f.upload("second");
    await f.upload("third");
    const replay = await f.upload("replay", {}, undefined, { "idempotency-key": key });
    expect(replay.status).toBe(202);
    expect(((await replay.json()) as { source_id: string }).source_id).toBe(
      ((await saved.json()) as { source_id: string }).source_id,
    );
    expect((await f.upload("fourth")).status).toBe(429);
  });
  test("an idempotent replay cannot report a deleted source as accepted", async () => {
    const f = await fixture();
    const key = randomUUID();
    const saved = await f.upload("replay", {}, undefined, { "idempotency-key": key });
    const { source_id: id } = (await saved.json()) as { source_id: string };
    const etag = (await f.req(`/api/v1/sources/${id}`)).headers.get("etag") ?? "";
    await f.req(`/api/v1/sources/${id}`, "DELETE", undefined, client, { "if-match": etag });
    expect((await f.upload("replay", {}, undefined, { "idempotency-key": key })).status).toBe(404);
  });
  test("credential revocation during multipart reading also rejects idempotent replays", async () => {
    const f = await fixture();
    const key = randomUUID();
    await f.upload("replay", {}, undefined, { "idempotency-key": key });
    const boundary = "review-multipart-boundary";
    const bytes = new TextEncoder().encode(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="notes.txt"\r\nContent-Type: text/plain\r\n\r\nreplay\r\n--${boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{"title":"Notes","collection_id":"wiki"}\r\n--${boundary}--\r\n`,
    );
    let release = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        release = () => {
          controller.enqueue(bytes);
          controller.close();
        };
      },
    });
    const pending = f.app.request(
      new Request("http://localhost/api/v1/sources", {
        method: "POST",
        headers: {
          ...headers(),
          "content-type": `multipart/form-data; boundary=${boundary}`,
          "idempotency-key": key,
        },
        body: stream,
      }),
    );
    await Bun.sleep(20);
    f.setClients([]);
    release();
    expect((await pending).status).toBe(401);
  });
  test("large supported embedding dimensions stay within the HTTP response budget", async () => {
    const batches: number[] = [];
    const server = Bun.serve({
      hostname: "127.0.0.1",
      port: 0,
      async fetch(request) {
        const { input } = (await request.json()) as { input: string[] };
        batches.push(input.length);
        return Response.json({
          profile: "large-model",
          vectors: input.map(() => Array.from({ length: 8192 }, () => 0.0001234567890123456)),
        });
      },
    });
    try {
      const provider = new GatewayEmbedding(
        `http://127.0.0.1:${server.port}`,
        "test",
        "large-model",
        8192,
      );
      const f = await fixture({ embedding: provider });
      const saved = await f.ready(
        Array.from({ length: 17 }, (_, i) => `paragraph ${i}`).join("\n\n"),
      );
      expect(f.store.job(saved.job_id)?.state).toBe("completed");
      expect(batches).toEqual([8, 8, 1]);
    } finally {
      await server.stop(true);
    }
  });
  test("answer provider deadlines are reported as answer_timeout", async () => {
    const f = await fixture({
      answer: {
        async generate(_question, _evidence, signal) {
          return new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () => reject(signal.reason), { once: true });
          });
        },
      },
    });
    await f.ready();
    const accepted = await f.req("/api/v1/answers", "POST", {
      query: "在庫",
      scope: { collection_ids: ["wiki"] },
      timeout_ms: 20,
    });
    const { answer_id } = (await accepted.json()) as { answer_id: string };
    await f.worker.tick();
    expect(f.store.job(answer_id)?.error_code).toBe("answer_timeout");
  });
});

describe("review job lifecycle", () => {
  test("retry resets source failure state and reports the index as pending", async () => {
    const f = await fixture();
    f.extractor.extract = async () => {
      throw new Error("extraction failure");
    };
    const accepted = await f.upload();
    const saved = (await accepted.json()) as { job_id: string; source_id: string };
    await f.worker.tick();
    expect(f.store.revision(f.store.source(saved.source_id)?.current_revision ?? "")?.state).toBe(
      "failed",
    );
    expect((await f.req(`/api/v1/jobs/${saved.job_id}/retry`, "POST")).status).toBe(202);
    expect((await f.search()).status).toBe(503);
  });
  test("stopping an extractor that ignores abort cannot publish a completed index", async () => {
    const f = await fixture();
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let started = () => {};
    const entered = new Promise<void>((resolve) => {
      started = resolve;
    });
    f.extractor.pause = () => {
      started();
      return gate;
    };
    const accepted = await f.upload();
    const saved = (await accepted.json()) as { job_id: string; source_revision: string };
    const processing = f.worker.tick();
    await entered;
    f.worker.stop();
    release();
    await processing;
    expect(f.store.job(saved.job_id)?.error_code).toBe("interrupted");
    expect(f.store.revision(saved.source_revision)?.fts_ready).toBe(0);
  });
  test("stopping a generation that ignores abort cannot save or complete its answer", async () => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let started = () => {};
    const entered = new Promise<void>((resolve) => {
      started = resolve;
    });
    const f = await fixture({
      answer: {
        async generate(_query, evidence) {
          started();
          await gate;
          return {
            answer: "old generation",
            citation_ids: [evidence[0]?.evidence_id ?? ""],
            unknowns: [],
          };
        },
      },
    });
    await f.ready();
    const accepted = await f.req("/api/v1/answers", "POST", {
      query: "在庫",
      scope: { collection_ids: ["wiki"] },
    });
    const saved = (await accepted.json()) as { answer_id: string };
    const processing = f.worker.tick();
    await entered;
    f.worker.stop();
    release();
    await processing;
    expect(f.store.job(saved.answer_id)?.error_code).toBe("interrupted");
    expect(
      ((await (await f.req(`/api/v1/answers/${saved.answer_id}`)).json()) as { result: unknown })
        .result,
    ).toBeNull();
  });
});

test("write-only clients can retry their scoped jobs while job reads remain forbidden", async () => {
  const f = await fixture();
  f.extractor.extract = async () => {
    throw new Error("failed");
  };
  const saved = (await (await f.upload()).json()) as { job_id: string };
  await f.worker.tick();
  f.setClients([{ ...client, actions: ["write"] }]);
  expect((await f.req(`/api/v1/jobs/${saved.job_id}`)).status).toBe(403);
  expect((await f.req(`/api/v1/jobs/${saved.job_id}/retry`, "POST")).status).toBe(202);
});
test("concurrent retry requests transition a failed job only once", async () => {
  const f = await fixture();
  f.extractor.extract = async () => {
    throw new Error("failed");
  };
  const saved = (await (await f.upload()).json()) as { job_id: string };
  await f.worker.tick();
  const responses = await Promise.all(
    Array.from({ length: 2 }, () =>
      f.req(`/api/v1/jobs/${saved.job_id}/retry`, "POST", { allow_ocr_resubmit: false }),
    ),
  );
  expect(responses.map((r) => r.status).sort()).toEqual([202, 409]);
});

test("the retry contract accepts omitted JSON bodies just like the running route", async () => {
  const f = await fixture();
  const response = await f.req("/api/v1/openapi.json");
  const spec = (await response.json()) as {
    paths: Record<string, { post: { requestBody: { required: boolean } } }>;
  };
  expect(spec.paths["/api/v1/jobs/{job_id}/retry"]?.post.requestBody.required).toBe(false);
});

const azureProfile = {
  provider: "azure_read" as const,
  enabled: true,
  endpoint: "https://test.cognitiveservices.azure.com",
  auth: "api_key" as const,
  client_id: null,
  api_version: "2024-11-30" as const,
  model: "prebuilt-read" as const,
  profile: "read-v1" as const,
  tier: "F0" as const,
  document_timeout: 1800,
  max_submissions: 100,
  adapter_version: "1" as const,
};
async function azureJob() {
  const f = await fixture({ persistent: true });
  const { job_id } = (await (await f.upload()).json()) as { job_id: string };
  f.store.db.run("UPDATE jobs SET payload=? WHERE id=?", [
    JSON.stringify({ profile: "azure-read-v1", ocr_profile: azureProfile }),
    job_id,
  ]);
  const job = f.store.claim();
  if (!job) throw new Error("missing job");
  const rev = f.store.revision(job.source_revision ?? "");
  const key = "a".repeat(64);
  const record = {
    key,
    state: "prepared" as const,
    attempt: 0,
    operation: null,
    response_sha256: null,
    error_code: null,
    metadata: {
      boundary: job.source_id,
      source_sha256: rev?.sha256,
      endpoint: azureProfile.endpoint,
      model: azureProfile.model,
      api_version: azureProfile.api_version,
    },
  };
  const state = f.store.ocrOperation(job, key, record);
  if (!state) throw new Error("missing operation");
  return { f, job, key, state };
}

test("OCR intent survives reopening, rejects stale runs and limits submissions before send", async () => {
  const { f, job, key, state } = await azureJob();
  const sending = f.store.ocrOperation(
    job,
    key,
    { ...state, state: "submitting", attempt: 1 },
    state.version,
    1,
  );
  expect(sending?.state).toBe("submitting");
  const reopened = new Store(join(f.root, "db.sqlite"));
  expect(reopened.ocrOperation(job, key)?.attempt).toBe(1);
  reopened.db.close();
  const key2 = "b".repeat(64);
  const second = f.store.ocrOperation(job, key2, { ...state, key: key2 }, 0);
  if (!second) throw new Error("missing operation");
  expect(() =>
    f.store.ocrOperation(
      job,
      key2,
      { ...second, state: "submitting", attempt: 1 },
      second.version,
      1,
    ),
  ).toThrow("ocr_submission_limit");
  f.store.db.run("UPDATE jobs SET lease_until=0 WHERE id=?", [job.id]);
  const fresh = f.store.claim();
  if (!fresh || !sending) throw new Error("missing reclaimed job");
  expect(() => f.store.ocrOperation(job, key)).toThrow("ocr_cancelled");
  expect(() =>
    f.store.ocrOperation(fresh, key, { ...sending, state: "prepared" }, sending.version),
  ).toThrow("ocr_state_conflict");
});

test("OCR unknown receipt needs explicit acknowledgement consumed before one resend", async () => {
  const { f, job, key, state } = await azureJob();
  const sending = f.store.ocrOperation(
    job,
    key,
    { ...state, state: "submitting", attempt: 1 },
    state.version,
  );
  if (!sending) throw new Error("missing intent");
  const unknown = f.store.ocrOperation(
    job,
    key,
    { ...sending, state: "submission_unknown", error_code: "submission_unknown" },
    sending.version,
  );
  if (!unknown) throw new Error("missing operation");
  f.store.finish(job, "failed", "submission_unknown");
  expect((await f.req(`/api/v1/jobs/${job.id}/retry`, "POST")).status).toBe(409);
  expect(
    (await f.req(`/api/v1/jobs/${job.id}/retry`, "POST", { allow_ocr_resubmit: true })).status,
  ).toBe(202);
  const next = f.store.claim();
  if (!next) throw new Error("missing job");
  const prepared = f.store.ocrOperation(
    next,
    key,
    { ...unknown, state: "prepared", error_code: null },
    unknown.version,
  );
  expect(JSON.parse(f.store.job(next.id)?.payload ?? "{}").allow_ocr_resubmit).toBe(false);
  if (!prepared) throw new Error("missing operation");
  const resend = f.store.ocrOperation(
    next,
    key,
    { ...prepared, state: "submitting", attempt: 2 },
    prepared.version,
  );
  if (!resend) throw new Error("missing intent");
  expect(() =>
    f.store.ocrOperation(next, key, { ...resend, state: "prepared" }, resend.version),
  ).toThrow("ocr_state_conflict");
});

test("re-extraction failure retains published evidence and a successful retry switches text", async () => {
  const f = await fixture();
  const saved = await f.ready("original searchable text");
  (f.extractor as Extractor).ocrProfile = async () => ({
    profile: "azure-read-v1",
    ocr_profile: azureProfile,
  });
  const etag = (await f.req(`/api/v1/sources/${saved.source_id}`)).headers.get("etag") ?? "";
  const original = f.extractor.extract.bind(f.extractor);
  f.extractor.extract = async () => {
    throw new ApiError(422, "ocr_http_401");
  };
  const accepted = await f.req(
    `/api/v1/sources/${saved.source_id}/reextract`,
    "POST",
    { ocr_provider: "azure_read" },
    client,
    { "if-match": etag },
  );
  expect(accepted.status).toBe(202);
  await f.worker.tick();
  expect(f.store.revision(saved.source_revision)?.state).toBe("ready");
  expect((await body(await f.search("original"))).results).toHaveLength(1);
  const { job_id } = (await accepted.json()) as { job_id: string };
  const evidence = {
    picture_ref: "#/pictures/0",
    provider: "azure_read",
    coordinate_frame: "embedded_image_pixels",
  };
  f.extractor.extract = async (job, revision) => {
    const data = await original(job, revision);
    return {
      ...data,
      profile: "azure-read-v1",
      contexts: data.contexts.map((c) => ({
        ...c,
        text: "OCR NEW NEEDLE",
        ocr_evidence: [evidence],
      })),
      chunks: data.chunks.map((c) => ({ ...c, text: "OCR NEW NEEDLE", ocr_evidence: [evidence] })),
    };
  };
  expect((await f.req(`/api/v1/jobs/${job_id}/retry`, "POST")).status).toBe(202);
  await f.worker.tick();
  expect(f.store.job(job_id)?.state).toBe("completed");
  expect((await body(await f.search("original"))).results).toHaveLength(0);
  const found = await body(await f.search("NEEDLE"));
  expect(found.results).toHaveLength(1);
  expect(
    (found.results[0] as unknown as { locator: { ocr_evidence: unknown[] } }).locator.ocr_evidence,
  ).toEqual([evidence]);
  const calls = f.extractor.calls;
  const newEtag = (await f.req(`/api/v1/sources/${saved.source_id}`)).headers.get("etag") ?? "";
  expect(
    (
      await f.req(`/api/v1/sources/${saved.source_id}/reindex`, "POST", undefined, client, {
        "if-match": newEtag,
      })
    ).status,
  ).toBe(202);
  await f.worker.tick();
  expect(f.extractor.calls).toBe(calls);
});

test("repeated query words do not hide unique words within the token budget", async () => {
  const f = await fixture();
  await f.ready("ユニークな仕様です");
  const result = await body(await f.search(`${"無関係 ".repeat(32)}ユニーク`));
  expect(result.results).toHaveLength(1);
});

test("private OCR callback requires its worker token and fences deleted or superseded runs", async () => {
  const { f, job, key } = await azureJob();
  const token = "worker-private-token-".repeat(3);
  const internal = createApi({
    store: f.store,
    artifactRoot: f.root,
    clients: () => [client],
    extractor: f.extractor,
    workerToken: token,
  });
  const invoke = (auth: string, run = job.run) =>
    internal.app.request(`/internal/v1/jobs/${job.id}/ocr`, {
      method: "POST",
      headers: { authorization: `Bearer ${auth}`, "content-type": "application/json" },
      body: JSON.stringify({ action: "get", key, run, source_revision: job.source_revision }),
    });
  expect((await invoke(client.token)).status).toBe(401);
  expect((await invoke("é".repeat(token.length))).status).toBe(401);
  expect((await invoke(token)).status).toBe(200);
  f.store.db.run("UPDATE jobs SET lease_until=0 WHERE id=?", [job.id]);
  const fresh = f.store.claim();
  if (!fresh) throw new Error("missing job");
  expect((await invoke(token)).status).toBe(409);
  expect((await invoke(token, fresh.run)).status).toBe(200);
  const etag = (await f.req(`/api/v1/sources/${job.source_id}`)).headers.get("etag") ?? "";
  expect(
    (
      await f.req(`/api/v1/sources/${job.source_id}`, "DELETE", undefined, client, {
        "if-match": etag,
      })
    ).status,
  ).toBe(202);
  expect((await invoke(token, fresh.run)).status).toBe(409);
  await f.worker.tick();
  expect(f.store.db.query("SELECT * FROM ocr_operations").all()).toHaveLength(0);
});

async function filesUnder(dir: string): Promise<string[]> {
  const names = await readdir(dir).catch(() => []);
  const found: string[] = [];
  for (const name of names) {
    const path = join(dir, name);
    try {
      await readdir(path);
    } catch {
      found.push(path);
      continue;
    }
    found.push(...(await filesUnder(path)));
  }
  return found;
}

test("a crash before registration leaves no source and removes the original", async () => {
  const f = await fixture();
  process.env.DOCLING_QUALITY_FAULT = "original_written";
  const response = await f.upload();
  expect(response.status).toBe(500);
  expect(JSON.stringify(await response.json())).not.toContain("original_written");
  expect(f.extractor.calls).toBe(0);
  expect(f.store.db.query("SELECT COUNT(*) AS n FROM sources").get()).toEqual({ n: 0 });
  expect(await filesUnder(join(f.root, "sources"))).toEqual([]);
});

test("a crash during registration commit leaves no source and removes the original", async () => {
  const f = await fixture();
  process.env.DOCLING_QUALITY_FAULT = "register_before_commit";
  const response = await f.upload();
  expect(response.status).toBe(500);
  expect(f.store.db.query("SELECT COUNT(*) AS n FROM sources").get()).toEqual({ n: 0 });
  expect(f.store.db.query("SELECT COUNT(*) AS n FROM revisions").get()).toEqual({ n: 0 });
  expect(await filesUnder(join(f.root, "sources"))).toEqual([]);
});

test("the ninth concurrent search is refused and a later search still runs", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let entered = 0;
  const f = await fixture({
    searchGate: async () => {
      entered += 1;
      await gate;
    },
  });
  const body = JSON.stringify({
    query: "在庫",
    mode: "text",
    scope: { collection_ids: ["wiki"] },
  });
  const send = () =>
    f.app.request("/api/v1/search", {
      method: "POST",
      headers: { ...headers(), "content-type": "application/json" },
      body,
    });
  const pending = Array.from({ length: 8 }, send);
  for (let attempt = 0; attempt < 50 && entered < 8; attempt++)
    await new Promise((resolve) => setTimeout(resolve, 10));
  expect(entered).toBe(8);
  const blocked = await send();
  expect(blocked.status).toBe(429);
  expect((await blocked.json()).error.code).toBe("search_busy");
  release();
  expect((await Promise.all(pending)).every((response) => response.status === 200)).toBe(true);
  expect((await f.search()).status).toBe(200);
});

test("JSON bodies over the limit are refused with or without content-length", async () => {
  const f = await fixture();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(MAX_JSON + 1));
      controller.close();
    },
  });
  const undeclared = await f.app.request("/api/v1/search", {
    method: "POST",
    headers: { ...headers(), "content-type": "application/json" },
    body: stream,
  });
  expect(undeclared.status).toBe(413);
  expect((await undeclared.json()).error.code).toBe("request_too_large");
  const declared = await f.app.request("/api/v1/sources", {
    method: "POST",
    headers: {
      ...headers(),
      "content-type": "multipart/form-data; boundary=bound",
      "content-length": String(MAX_FILE + 1024 * 1024 + 1),
      "idempotency-key": "size-boundary",
    },
    body: "",
  });
  expect(declared.status).toBe(413);
  expect((await declared.json()).error.code).toBe("request_too_large");
});

describe("versioned viewer publication", () => {
  const prefix = `/viewer/session/${"x".repeat(43)}/`;
  test("only the accepted attempt is read and stale/foreign references are rejected", async () => {
    const f = await fixture();
    f.extractor.viewerReady = true;
    let calls = 0;
    f.extractor.viewer = async (input) => {
      calls++;
      return {
        job: {
          id: input.job_id,
          filename: "notes.txt",
          state: "success",
          pages: 0,
          preview: "text.html",
          slide_layout: false,
        },
        kind: "document",
        units: 1,
      };
    };
    const registered = await f.ready();
    const source = await (await f.req(`/api/v1/sources/${registered.source_id}`)).json();
    const ref = {
      source_id: source.source_id,
      source_revision: source.source_revision,
      evidence_revision: source.evidence_revision,
      prefix,
    };
    expect((await f.req("/api/v1/viewer", "POST", ref)).status).toBe(200);
    expect(calls).toBe(1);
    expect(f.extractor.calls).toBe(1);
    expect((await f.req("/api/v1/viewer", "POST", ref, other)).status).toBe(404);
    expect(
      (await f.req("/api/v1/viewer", "POST", { ...ref, evidence_revision: randomUUID() })).status,
    ).toBe(409);
    expect(
      (await f.req("/api/v1/viewer", "POST", { ...ref, prefix: "https://evil.invalid/" })).status,
    ).toBe(422);
    expect(calls).toBe(1);
    const metadata = await f.req(`/api/v1/sources/${source.source_id}`);
    expect(
      (
        await f.req(`/api/v1/sources/${source.source_id}`, "DELETE", undefined, client, {
          "if-match": metadata.headers.get("etag") ?? "",
        })
      ).status,
    ).toBe(202);
    expect(
      (await f.req("/api/v1/viewer", "POST", { ...ref, resource: "file/image.png" })).status,
    ).toBe(404);
    expect(calls).toBe(1);
  });
  test("a deletion racing an asset read cannot return bytes", async () => {
    const f = await fixture();
    f.extractor.viewerReady = true;
    const registered = await f.ready();
    const source = await (await f.req(`/api/v1/sources/${registered.source_id}`)).json();
    f.extractor.viewer = async () => {
      const metadata = await f.req(`/api/v1/sources/${source.source_id}`);
      await f.req(`/api/v1/sources/${source.source_id}`, "DELETE", undefined, client, {
        "if-match": metadata.headers.get("etag") ?? "",
      });
      return { media_type: "image/png", body_base64: "cHJpdmF0ZQ==" };
    };
    const response = await f.req("/api/v1/viewer", "POST", {
      source_id: source.source_id,
      source_revision: source.source_revision,
      evidence_revision: source.evidence_revision,
      prefix,
      resource: "file/image.png",
    });
    expect(response.status).toBe(404);
    expect(await response.text()).not.toContain("cHJpdmF0ZQ==");
  });
  test("search readiness does not imply preview readiness", async () => {
    const f = await fixture();
    const registered = await f.ready();
    const source = await (await f.req(`/api/v1/sources/${registered.source_id}`)).json();
    const response = await f.req("/api/v1/viewer", "POST", {
      source_id: source.source_id,
      source_revision: source.source_revision,
      evidence_revision: source.evidence_revision,
      prefix,
    });
    expect(response.status).toBe(409);
    expect((await response.json()).error.code).toBe("preview_unavailable");
  });
});

test("viewer evidence switches atomically and failed re-extraction preserves the prior preview", async () => {
  const f = await fixture();
  f.extractor.viewerReady = true;
  (f.extractor as Extractor).ocrProfile = async () => ({
    profile: "local-v1",
    ocr_profile: { ...azureProfile, provider: "local", enabled: false, endpoint: "" },
  });
  f.extractor.viewer = async () => ({ media_type: "text/html", body_base64: "b2s=" });
  const registered = await f.ready();
  const source = await (await f.req(`/api/v1/sources/${registered.source_id}`)).json();
  const reference = {
    source_id: source.source_id,
    source_revision: source.source_revision,
    evidence_revision: source.evidence_revision,
    prefix: `/viewer/session/${"x".repeat(43)}/`,
    resource: "view/document",
  };
  const metadata = await f.req(`/api/v1/sources/${source.source_id}`);
  await f.req(
    `/api/v1/sources/${source.source_id}/reextract`,
    "POST",
    { ocr_provider: "local" },
    client,
    {
      "if-match": metadata.headers.get("etag") ?? "",
    },
  );
  f.extractor.pause = async () => {
    throw new ApiError(503, "provider_unavailable");
  };
  await f.worker.tick();
  expect((await f.req("/api/v1/viewer", "POST", reference)).status).toBe(200);
  const latest = await f.req(`/api/v1/sources/${source.source_id}`);
  f.extractor.pause = undefined;
  await f.req(
    `/api/v1/sources/${source.source_id}/reextract`,
    "POST",
    { ocr_provider: "local" },
    client,
    {
      "if-match": latest.headers.get("etag") ?? "",
    },
  );
  await f.worker.tick();
  expect((await f.req("/api/v1/viewer", "POST", reference)).status).toBe(409);
  const next = await (await f.req(`/api/v1/sources/${source.source_id}`)).json();
  expect(
    (
      await f.req("/api/v1/viewer", "POST", {
        ...reference,
        evidence_revision: next.evidence_revision,
      })
    ).status,
  ).toBe(200);
});
