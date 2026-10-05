import { randomUUID, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, rm, statfs } from "node:fs/promises";
import { basename, extname, join } from "node:path";
import { Hono } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { z } from "zod";
import { authenticate, authorized, type Clients, narrow, refresh, requireAction } from "./auth";
import {
  ApiError,
  answerSchema,
  contextSchema,
  id,
  MAX_FILE,
  MAX_JSON,
  metadataSchema,
  type Principal,
  parse,
  searchSchema,
  viewerRequestSchema,
  viewerResultSchema,
} from "./contracts";
import { ocrProvider, ocrRequest } from "./ocr";
import type { AnswerProvider, EmbeddingProvider, Extractor } from "./providers";
import { checkpoint } from "./quality";
import { errorResponse, responseSchemas } from "./responses";
import { Retrieval } from "./retrieval";
import { nowISO, type SourceRow, type Store } from "./store";
import { readAnswer, Worker } from "./worker";

export type ApiOptions = {
  store: Store;
  artifactRoot: string;
  clients: Clients;
  extractor: Extractor;
  embedding?: EmbeddingProvider;
  answer?: AnswerProvider;
  workerToken?: string;
  ocrMonthlySubmissions?: number;
  searchGate?: () => Promise<void>;
};
type Env = { Variables: { principal: Principal; requestId: string } };
const suffixes = new Set([".pdf", ".pptx", ".xlsx", ".docx", ".md", ".markdown", ".txt", ".text"]);
export const ROUTES = [
  ["post", "/api/v1/search"],
  ["post", "/api/v1/viewer"],
  ["post", "/api/v1/context"],
  ["post", "/api/v1/sources"],
  ["get", "/api/v1/sources/{source_id}"],
  ["get", "/api/v1/sources/{source_id}/content"],
  ["post", "/api/v1/sources/{source_id}/revisions"],
  ["delete", "/api/v1/sources/{source_id}"],
  ["post", "/api/v1/sources/{source_id}/reindex"],
  ["post", "/api/v1/sources/{source_id}/reextract"],
  ["get", "/api/v1/jobs/{job_id}"],
  ["post", "/api/v1/jobs/{job_id}/retry"],
  ["post", "/api/v1/answers"],
  ["get", "/api/v1/answers/{answer_id}"],
  ["get", "/health/live"],
  ["get", "/health/ready"],
  ["get", "/api/v1/openapi.json"],
] as const;

async function boundedBody(request: Request, max: number) {
  if (Number(request.headers.get("content-length") ?? 0) > max)
    throw new ApiError(413, "request_too_large");
  const reader = request.body?.getReader();
  if (!reader) return Buffer.alloc(0);
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > max) throw new ApiError(413, "request_too_large");
      chunks.push(value);
    }
    return Buffer.concat(chunks);
  } catch (e) {
    await reader.cancel().catch(() => {});
    throw e;
  }
}
async function json(request: Request) {
  if (!request.headers.get("content-type")?.startsWith("application/json"))
    throw new ApiError(415, "json_required");
  try {
    return JSON.parse((await boundedBody(request, MAX_JSON)).toString());
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(422, "invalid_json");
  }
}
async function multipart(request: Request) {
  if (!request.headers.get("content-type")?.startsWith("multipart/form-data;"))
    throw new ApiError(415, "multipart_required");
  const bytes = await boundedBody(request, MAX_FILE + 1024 * 1024);
  let form: FormData;
  try {
    form = await new Request(request.url, {
      method: "POST",
      headers: request.headers,
      body: bytes,
    }).formData();
  } catch {
    throw new ApiError(422, "invalid_multipart");
  }
  if (
    form.getAll("file").length !== 1 ||
    form.getAll("metadata").length !== 1 ||
    form.getAll("ocr_provider").length > 1 ||
    [...form.keys()].some((k) => k !== "file" && k !== "metadata" && k !== "ocr_provider")
  )
    throw new ApiError(422, "invalid_multipart");
  const file = form.get("file"),
    raw = form.get("metadata");
  if (!(file instanceof File) || typeof raw !== "string" || raw.length > 16000)
    throw new ApiError(422, "invalid_multipart");
  if (!file.size || file.size > MAX_FILE) throw new ApiError(413, "invalid_file_size");
  let metadata: unknown;
  try {
    metadata = JSON.parse(raw);
  } catch {
    throw new ApiError(422, "invalid_metadata");
  }
  const m = parse(metadataSchema, metadata);
  const filename = [...basename(file.name.replaceAll("\\", "/"))]
    .filter((c) => c.charCodeAt(0) >= 32 && c.charCodeAt(0) !== 127)
    .join("");
  const suffix = extname(filename).toLowerCase();
  if (!suffixes.has(suffix) || filename.length > 256) throw new ApiError(415, "unsupported_format");
  if (m.source_kind === "wiki" && ![".md", ".markdown", ".txt", ".text"].includes(suffix))
    throw new ApiError(422, "wiki_text_required");
  const provider = form.has("ocr_provider")
    ? parse(ocrProvider, form.get("ocr_provider"))
    : undefined;
  return { file, metadata: m, filename, suffix, provider };
}

export function createApi(options: ApiOptions) {
  const { store, artifactRoot, clients, extractor } = options;
  const retrieval = new Retrieval(store, clients, options.embedding);
  const worker = new Worker(
    store,
    artifactRoot,
    clients,
    extractor,
    retrieval,
    options.embedding,
    options.answer,
  );
  const app = new Hono<Env>();
  const rate = new Map<string, { since: number; read: number; write: number; answer: number }>();
  let searching = 0;
  let uploading = 0;
  app.use("*", async (c, next) => {
    c.set("requestId", randomUUID());
    c.header("X-Request-Id", c.get("requestId"));
    c.header("Cache-Control", "no-store");
    c.header("X-Content-Type-Options", "nosniff");
    c.header("Content-Security-Policy", "default-src 'none'; sandbox");
    await next();
  });
  app.use("/api/v1/*", async (c, next) => {
    if (c.req.header("origin")) throw new ApiError(403, "server_to_server_only");
    const p = authenticate(c.req.raw.headers, clients());
    c.set("principal", p);
    let row = rate.get(p.clientId);
    if (!row || row.since + 60000 < Date.now()) {
      row = { since: Date.now(), read: 0, write: 0, answer: 0 };
      rate.set(p.clientId, row);
    }
    const category =
      c.req.method === "POST" && c.req.path === "/api/v1/answers"
        ? "answer"
        : c.req.method === "GET" ||
            ["/api/v1/search", "/api/v1/context", "/api/v1/viewer"].includes(c.req.path)
          ? "read"
          : "write";
    row[category]++;
    if (row[category] > { read: 600, write: 60, answer: 12 }[category])
      throw new ApiError(429, "rate_limited", "Rate limit exceeded", true);
    await next();
  });
  app.post("/internal/v1/jobs/:job_id/ocr", async (c) => {
    const wanted = options.workerToken;
    const header = c.req.header("authorization") ?? "";
    const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
    if (
      !wanted ||
      Buffer.byteLength(supplied) !== Buffer.byteLength(wanted) ||
      !timingSafeEqual(Buffer.from(supplied), Buffer.from(wanted))
    )
      throw new ApiError(401, "unauthenticated");
    const value = parse(ocrRequest, await json(c.req.raw));
    const job = store.job(parse(id, c.req.param("job_id")));
    if (!job || job.run !== value.run || job.source_revision !== value.source_revision)
      throw new ApiError(409, "ocr_cancelled");
    if (value.action === "save" && (!value.record || value.expected_version === undefined))
      throw new ApiError(422, "ocr_state_invalid");
    return c.json({
      record: store.ocrOperation(
        job,
        value.key,
        value.action === "save" ? value.record : undefined,
        value.expected_version,
        options.ocrMonthlySubmissions ?? 10000,
      ),
    });
  });
  app.onError((e, c) => {
    const error = e instanceof ApiError ? e : new ApiError(500, "internal_error");
    if (error.status === 429) c.header("Retry-After", "60");
    return c.json(
      {
        request_id: c.get("requestId"),
        error: {
          code: error.code,
          message: error.message,
          retryable: error.retryable,
          ...(error.details ? { details: error.details } : {}),
        },
      },
      error.status as ContentfulStatusCode,
    );
  });
  app.notFound((c) =>
    c.json(
      {
        request_id: c.get("requestId"),
        error: { code: "not_found", message: "not_found", retryable: false },
      },
      404,
    ),
  );
  app.get("/health/live", (c) => c.json({ request_id: c.get("requestId"), status: "alive" }));
  app.get("/health/ready", async (c) => {
    try {
      if (!clients().length) throw new Error();
      store.db.query("SELECT 1").get();
      const disk = await statfs(artifactRoot);
      if (disk.bavail * disk.bsize < 2 * 1024 ** 3) throw new Error();
      return c.json({
        request_id: c.get("requestId"),
        status: "ready",
        capabilities: {
          text: true,
          semantic: !!options.embedding,
          answers: !!options.answer,
          source_management: extractor.ready ? await extractor.ready() : true,
          history: true,
        },
      });
    } catch {
      throw new ApiError(503, "not_ready", "API is not ready", true);
    }
  });
  app.get("/api/v1/openapi.json", (c) => {
    requireAction(c.get("principal"), "read");
    return c.json({ ...openapi(), request_id: c.get("requestId") });
  });
  app.post("/api/v1/search", async (c) => {
    const p = c.get("principal");
    requireAction(p, "read");
    if (searching >= 8) throw new ApiError(429, "search_busy", "Search concurrency limit", true);
    searching++;
    try {
      if (options.searchGate) await options.searchGate();
      const input = parse(searchSchema, await json(c.req.raw));
      return c.json({ request_id: c.get("requestId"), ...(await retrieval.search(p, input)) });
    } finally {
      searching--;
    }
  });
  app.post("/api/v1/context", async (c) => {
    const input = parse(contextSchema, await json(c.req.raw));
    return c.json({
      request_id: c.get("requestId"),
      ...retrieval.context(
        c.get("principal"),
        input.retrieval_id,
        input.references,
        input.max_chars,
      ),
    });
  });
  app.get("/api/v1/sources/:source_id", (c) => {
    const p = c.get("principal");
    requireAction(p, "read");
    const s = store.getSource(parse(id, c.req.param("source_id")), p);
    const r = store.revision(s.current_revision);
    c.header("ETag", store.etag(s));
    return c.json({
      request_id: c.get("requestId"),
      source_id: s.id,
      title: s.title,
      source_kind: s.source_kind,
      language: s.language,
      scope: { collection_id: s.collection_id, project_id: s.project_id, region: s.region },
      source_revision: s.current_revision,
      evidence_revision: r?.evidence_revision ?? null,
      state: r?.state ?? "queued",
      fts_ready: !!r?.fts_ready,
      semantic_ready: !!r?.semantic_ready,
      filename: r?.filename,
      warnings: r ? JSON.parse(r.warnings) : [],
      created_at: s.created_at,
      source_url: `/api/v1/sources/${s.id}/content?revision=${s.current_revision}`,
    });
  });
  app.get("/api/v1/sources/:source_id/content", async (c) => {
    const p = c.get("principal");
    requireAction(p, "read");
    const s = store.getSource(parse(id, c.req.param("source_id")), p);
    const revisionId = parse(id, c.req.query("revision") ?? s.current_revision);
    const past = c.req.query("include_past_revisions") === "true";
    const r = store.revision(revisionId);
    if (!r || r.source_id !== s.id) throw new ApiError(404, "source_not_found");
    if (!past && s.current_revision !== r.id) throw new ApiError(409, "source_changed");
    let bytes: Buffer;
    try {
      bytes = await readFile(join(artifactRoot, "sources", s.id, r.id, `original${r.suffix}`));
    } catch {
      throw new ApiError(404, "source_not_found");
    }
    const current = store.getSource(s.id, refresh(p, clients()));
    if (!past && current.current_revision !== r.id) throw new ApiError(409, "source_changed");
    c.header("Content-Type", r.suffix === ".pdf" ? "application/pdf" : "application/octet-stream");
    c.header(
      "Content-Disposition",
      `attachment; filename="source${r.suffix}"; filename*=UTF-8''${encodeURIComponent(r.filename)}`,
    );
    return c.body(new Uint8Array(bytes).buffer);
  });
  app.post("/api/v1/viewer", async (c) => {
    const p = c.get("principal");
    requireAction(p, "read");
    const input = parse(viewerRequestSchema, await json(c.req.raw));
    const check = () => {
      const source = store.getSource(input.source_id, refresh(p, clients()));
      if (source.current_revision !== input.source_revision)
        throw new ApiError(409, "source_changed");
      const revision = store.revision(input.source_revision);
      if (!revision || revision.evidence_revision !== input.evidence_revision)
        throw new ApiError(409, "evidence_changed");
      const artifact = store.viewerArtifact(revision.id);
      if (!artifact || artifact.evidence_revision !== input.evidence_revision || !extractor.viewer)
        throw new ApiError(409, "preview_unavailable");
      return { revision, artifact };
    };
    const { revision, artifact } = check();
    const result = parse(
      viewerResultSchema,
      await extractor.viewer?.({
        source_id: input.source_id,
        source_revision: revision.id,
        suffix: revision.suffix,
        job_id: artifact.job_id,
        run: artifact.run,
        resource: input.resource,
        prefix: input.prefix,
      }),
    );
    check(); // Revoke even when delete/update/re-extract races the worker read.
    return c.json(result);
  });
  async function upload(request: Request, initial: Principal, route: string, existing?: SourceRow) {
    requireAction(initial, "write");
    const key = request.headers.get("idempotency-key");
    if (!key || !/^[\x21-\x7e]{1,128}$/.test(key))
      throw new ApiError(428, "idempotency_key_required");
    const { file, metadata, filename, suffix, provider } = await multipart(request);
    initial = refresh(initial, clients());
    requireAction(initial, "write");
    if (!authorized(initial, metadata)) throw new ApiError(403, "scope_forbidden");
    if (
      existing &&
      (metadata.title !== existing.title ||
        metadata.collection_id !== existing.collection_id ||
        (metadata.project_id ?? null) !== existing.project_id ||
        (metadata.region ?? null) !== existing.region ||
        metadata.source_kind !== existing.source_kind ||
        metadata.language !== existing.language ||
        (metadata.external_key ?? null) !== existing.external_key)
    )
      throw new ApiError(422, "revision_metadata_immutable");
    const bytes = await file.arrayBuffer();
    const hash = Bun.CryptoHasher.hash("sha256", bytes, "hex");
    const operation = JSON.stringify([initial.clientId, initial.subject, route, key]);
    const requestHash = Bun.CryptoHasher.hash(
      "sha256",
      JSON.stringify([metadata, filename, hash, provider ?? null]),
      "hex",
    );
    const replay = store.replay(operation, requestHash);
    if (replay) {
      store.getSource(replay.source_id ?? "", initial);
      return replay;
    }
    if (provider && provider !== "local" && !extractor.ocrProfile)
      throw new ApiError(503, "ocr_not_configured");
    const extraction = extractor.ocrProfile
      ? await extractor.ocrProfile(provider)
      : { profile: "local-v1" as const };
    if (existing)
      store.match(store.getSource(existing.id, initial), request.headers.get("if-match"));
    const replacing =
      existing &&
      store.db
        .query(
          "SELECT id FROM jobs WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
        )
        .get(existing.id)
        ? 1
        : 0;
    if (uploading + store.outstanding("extract") - replacing > 3)
      throw new ApiError(429, "queue_full", "Document queue is full", true);
    const disk = await statfs(artifactRoot);
    if (disk.bavail * disk.bsize < 2 * 1024 ** 3 + file.size * 4)
      throw new ApiError(507, "insufficient_storage");
    const sourceId = existing?.id ?? randomUUID();
    const revisionId = randomUUID();
    const folder = join(artifactRoot, "sources", sourceId, revisionId);
    await mkdir(folder, { recursive: true });
    let registered = false;
    try {
      await Bun.write(join(folder, `original${suffix}`), bytes);
      checkpoint("original_written");
      await extractor.validate({
        source_id: sourceId,
        source_revision: revisionId,
        suffix,
        sha256: hash,
      });
      const p = refresh(initial, clients());
      requireAction(p, "write");
      if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
      return store.tx(() => {
        const repeated = store.replay(operation, requestHash);
        if (repeated) return repeated;
        const result = store.register(
          p,
          metadata,
          filename,
          suffix,
          hash,
          sourceId,
          revisionId,
          existing,
          request.headers.get("if-match"),
          extraction,
        );
        store.remember(operation, requestHash, result);
        registered = true;
        return result;
      });
    } finally {
      if (!registered) await rm(folder, { recursive: true, force: true });
    }
  }
  async function limitedUpload(
    request: Request,
    p: Principal,
    route: string,
    existing?: SourceRow,
  ) {
    requireAction(p, "write");
    // Reserve bounded multipart memory. Persistent queue admission comes after
    // the replay lookup, so retries remain possible when the queue is full.
    if (uploading >= 3) throw new ApiError(429, "queue_full", "Upload concurrency limit", true);
    uploading++;
    try {
      return await upload(request, p, route, existing);
    } finally {
      uploading--;
    }
  }
  app.post("/api/v1/sources", async (c) =>
    c.json(
      {
        request_id: c.get("requestId"),
        ...(await limitedUpload(c.req.raw, c.get("principal"), "sources")),
      },
      202,
    ),
  );
  app.post("/api/v1/sources/:source_id/revisions", async (c) => {
    const p = c.get("principal"),
      s = store.getSource(parse(id, c.req.param("source_id")), p);
    return c.json(
      {
        request_id: c.get("requestId"),
        ...(await limitedUpload(c.req.raw, p, `revision:${s.id}`, s)),
      },
      202,
    );
  });
  app.delete("/api/v1/sources/:source_id", (c) => {
    const p = c.get("principal");
    requireAction(p, "write");
    return c.json(
      {
        request_id: c.get("requestId"),
        ...store.remove(parse(id, c.req.param("source_id")), p, c.req.header("if-match") ?? null),
      },
      202,
    );
  });
  app.post("/api/v1/sources/:source_id/reindex", (c) => {
    const p = c.get("principal");
    requireAction(p, "write");
    const s = store.getSource(parse(id, c.req.param("source_id")), p);
    const result = store.tx(() => {
      store.match(s, c.req.header("if-match") ?? null);
      if (store.outstanding("extract") >= 3) throw new ApiError(429, "queue_full");
      if (!store.revision(s.current_revision)?.fts_ready)
        throw new ApiError(409, "extraction_not_ready");
      if (
        store.db
          .query(
            "SELECT id FROM jobs WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
          )
          .get(s.id)
      )
        throw new ApiError(409, "job_running");
      store.db.run("UPDATE revisions SET semantic_ready=0,state='indexing' WHERE id=?", [
        s.current_revision,
      ]);
      return {
        source_id: s.id,
        job_id: store.enqueue("extract", s.id, s.current_revision, { profile: "local-v1" }),
      };
    });
    return c.json({ request_id: c.get("requestId"), ...result }, 202);
  });
  app.post("/api/v1/sources/:source_id/reextract", async (c) => {
    const p = c.get("principal");
    requireAction(p, "write");
    const s = store.getSource(parse(id, c.req.param("source_id")), p);
    const input = parse(z.object({ ocr_provider: ocrProvider }).strict(), await json(c.req.raw));
    if (!extractor.ocrProfile) throw new ApiError(503, "ocr_not_configured");
    const selection = await extractor.ocrProfile(input.ocr_provider);
    const jobId = store.tx(() => {
      const source = store.getSource(s.id, refresh(p, clients()));
      store.match(source, c.req.header("if-match") ?? null);
      if (store.outstanding("extract") >= 3) throw new ApiError(429, "queue_full");
      if (
        store.db
          .query(
            "SELECT id FROM jobs WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
          )
          .get(source.id)
      )
        throw new ApiError(409, "job_running");
      return store.enqueue("extract", source.id, source.current_revision, {
        ...selection,
        reextract: true,
      });
    });
    return c.json({ request_id: c.get("requestId"), source_id: s.id, job_id: jobId }, 202);
  });
  function getJob(jobId: string, p: Principal) {
    const job = store.job(parse(id, jobId));
    if (!job) throw new ApiError(404, "job_not_found");
    if (job.kind === "answer") readAnswer(store, job.id, p);
    else {
      const s = store.source(job.source_id ?? "");
      if (!s || !authorized(p, s) || (s.deleted && job.kind !== "delete"))
        throw new ApiError(404, "job_not_found");
    }
    return job;
  }
  app.get("/api/v1/jobs/:job_id", (c) => {
    requireAction(c.get("principal"), "read");
    const j = getJob(c.req.param("job_id"), c.get("principal"));
    return c.json({
      request_id: c.get("requestId"),
      job_id: j.id,
      source_id: j.source_id,
      source_revision: j.source_revision,
      kind: j.kind,
      state: j.state,
      stage: j.stage,
      error_code: j.error_code,
      created_at: j.created_at,
      updated_at: j.updated_at,
    });
  });
  app.post("/api/v1/jobs/:job_id/retry", async (c) => {
    const p = c.get("principal");
    requireAction(p, "write");
    const j = getJob(c.req.param("job_id"), p);
    const input = c.req.raw.body
      ? parse(
          z.object({ allow_ocr_resubmit: z.boolean().default(false) }).strict(),
          await json(c.req.raw),
        )
      : { allow_ocr_resubmit: false };
    store.tx(() => {
      const current = refresh(p, clients());
      requireAction(current, "write");
      const j = getJob(c.req.param("job_id"), current);
      if (j.kind === "answer" || j.state !== "failed") throw new ApiError(409, "job_not_retryable");
      if (
        j.kind === "extract" &&
        store.source(j.source_id ?? "")?.current_revision !== j.source_revision
      )
        throw new ApiError(409, "source_changed");
      if (store.outstanding(j.kind) >= 3) throw new ApiError(429, "queue_full");
      const payload = { ...JSON.parse(j.payload), allow_ocr_resubmit: input.allow_ocr_resubmit };
      if (j.error_code === "submission_unknown" && !input.allow_ocr_resubmit)
        throw new ApiError(
          409,
          "ocr_resubmit_acknowledgement_required",
          "Azure may already have accepted this image. Explicit resubmission may incur duplicate charges.",
        );
      if (j.kind === "extract")
        store.db.run(
          "UPDATE revisions SET state=CASE WHEN fts_ready=1 THEN 'indexing' ELSE 'queued' END WHERE id=?",
          [j.source_revision],
        );
      store.db.run(
        "UPDATE jobs SET state='queued',stage='queued',error_code=NULL,lease_until=0,updated_at=?,payload=? WHERE id=?",
        [nowISO(), JSON.stringify(payload), j.id],
      );
    });
    return c.json({ request_id: c.get("requestId"), job_id: j.id }, 202);
  });
  app.post("/api/v1/answers", async (c) => {
    if (!options.answer) throw new ApiError(503, "answer_not_configured");
    const p = c.get("principal");
    requireAction(p, "answer");
    requireAction(p, "read");
    const input = parse(answerSchema, await json(c.req.raw));
    narrow(p, input.scope);
    const answerId = store.tx(() => {
      if (store.outstanding("answer") >= 3) throw new ApiError(429, "answer_queue_full");
      const aid = store.enqueue("answer", null, null, { principal: p, input });
      store.db.run(
        "INSERT INTO answers(id,client_id,subject,state,payload,expires) VALUES(?,?,?,'queued',?,?)",
        [aid, p.clientId, p.subject, JSON.stringify(input), Date.now() + 30 * 86400000],
      );
      return aid;
    });
    return c.json({ request_id: c.get("requestId"), answer_id: answerId, job_id: answerId }, 202);
  });
  app.get("/api/v1/answers/:answer_id", (c) => {
    const p = c.get("principal");
    requireAction(p, "read");
    return c.json({
      request_id: c.get("requestId"),
      ...readAnswer(store, parse(id, c.req.param("answer_id")), p),
    });
  });
  return { app, store, worker, retrieval };
}

export function openapi() {
  const paths: Record<string, Record<string, unknown>> = {};
  const inputs: Record<string, z.ZodType> = {
    "/api/v1/search": searchSchema,
    "/api/v1/viewer": viewerRequestSchema,
    "/api/v1/context": contextSchema,
    "/api/v1/answers": answerSchema,
    "/api/v1/sources/{source_id}/reextract": z.object({ ocr_provider: ocrProvider }).strict(),
    "/api/v1/jobs/{job_id}/retry": z
      .object({ allow_ocr_resubmit: z.boolean().default(false) })
      .strict(),
  };
  for (const [method, path] of ROUTES) {
    const parameters: Record<string, unknown>[] = [...path.matchAll(/\{(\w+)\}/g)].map((m) => ({
      name: m[1],
      in: "path",
      required: true,
      schema: { type: "string", format: "uuid" },
    }));
    if (
      method === "delete" ||
      path.endsWith("/revisions") ||
      path.endsWith("/reindex") ||
      path.endsWith("/reextract")
    )
      parameters.push({
        name: "If-Match",
        in: "header",
        required: true,
        schema: { type: "string" },
      });
    if ((path === "/api/v1/sources" && method === "post") || path.endsWith("/revisions"))
      parameters.push({
        name: "Idempotency-Key",
        in: "header",
        required: true,
        schema: { type: "string" },
      });
    if (path.endsWith("/content"))
      parameters.push(
        { name: "revision", in: "query", schema: { type: "string", format: "uuid" } },
        {
          name: "include_past_revisions",
          in: "query",
          schema: { type: "boolean", default: false },
        },
      );
    const requestBody = inputs[path]
      ? {
          required: !path.endsWith("/retry"),
          content: {
            "application/json": {
              schema: z.toJSONSchema(inputs[path], { unrepresentable: "any" }),
            },
          },
        }
      : method === "post" && (path === "/api/v1/sources" || path.endsWith("/revisions"))
        ? {
            required: true,
            content: {
              "multipart/form-data": {
                schema: {
                  type: "object",
                  required: ["file", "metadata"],
                  properties: {
                    file: { type: "string", format: "binary" },
                    metadata: { type: "string", description: "JSON encoded SourceMetadata" },
                    ocr_provider: { type: "string", enum: ["local", "azure_read", "disabled"] },
                  },
                },
              },
            },
          }
        : undefined;
    const operations = paths[path] ?? {};
    paths[path] = operations;
    operations[method] = {
      operationId: `${method}_${path.replaceAll(/[^a-z0-9]/g, "_")}`,
      parameters,
      ...(requestBody ? { requestBody } : {}),
      security: path.startsWith("/health/")
        ? []
        : [{ serviceToken: [], actorAssertion: [] }, { serviceToken: [] }],
      description: path.startsWith("/health/")
        ? "Health check"
        : "Actor clients require both credentials. Service-token-only access is restricted to registered batch clients and their exact configured scopes.",
      responses: {
        [(method === "post" &&
          !["/api/v1/search", "/api/v1/context", "/api/v1/viewer"].includes(path)) ||
        method === "delete"
          ? "202"
          : "200"]: {
          description: "Success",
          headers: { "X-Request-Id": { schema: { type: "string", format: "uuid" } } },
          content: path.endsWith("/content")
            ? {
                "application/octet-stream": { schema: { type: "string", format: "binary" } },
                "application/pdf": { schema: { type: "string", format: "binary" } },
              }
            : {
                "application/json": {
                  schema: z.toJSONSchema(
                    responseSchemas[`${method} ${path}`] ?? z.record(z.string(), z.unknown()),
                    { unrepresentable: "any" },
                  ),
                },
              },
        },
        default: {
          description: "Error",
          content: { "application/json": { schema: { $ref: "#/components/schemas/Error" } } },
        },
      },
    };
  }
  return {
    openapi: "3.1.0",
    info: { title: "Docling Desk API", version: "0.1.0" },
    paths,
    components: {
      securitySchemes: {
        serviceToken: { type: "http", scheme: "bearer" },
        actorAssertion: { type: "apiKey", in: "header", name: "X-Knowledge-Actor" },
      },
      schemas: {
        SourceMetadata: z.toJSONSchema(metadataSchema, { unrepresentable: "any" }),
        Error: z.toJSONSchema(errorResponse),
      },
    },
  };
}
