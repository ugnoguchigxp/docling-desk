import type { Database } from "bun:sqlite";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { authorized, grantKey, narrow } from "./auth";
import {
  ApiError,
  type Extraction,
  type Metadata,
  type Principal,
  queryTokens,
  type Reference,
  type SearchInput,
} from "./contracts";
import type { OcrRecord, ProfileSelection } from "./ocr";
import { checkpoint } from "./quality";
import { writerDatabase } from "./writer";

export type SourceRow = {
  id: string;
  title: string;
  collection_id: string;
  project_id: string | null;
  region: string | null;
  source_kind: "document" | "wiki";
  language: string;
  current_revision: string;
  deleted: number;
  generation: number;
  created_at: string;
  owner_client: string;
  external_key: string | null;
};
export type RevisionRow = {
  id: string;
  source_id: string;
  filename: string;
  suffix: string;
  sha256: string;
  evidence_revision: string | null;
  state: string;
  fts_ready: number;
  semantic_ready: number;
  created_at: string;
  indexed_at: string | null;
  warnings: string;
};
export type JobRow = {
  id: string;
  source_id: string | null;
  source_revision: string | null;
  kind: "extract" | "delete" | "answer";
  state: string;
  stage: string;
  run: number;
  lease_until: number;
  payload: string;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};
export type ChunkRow = SourceRow & {
  chunk_id: string;
  context_id: string;
  source_revision: string;
  evidence_revision: string;
  text: string;
  body: string;
  sha256: string;
  vector: string | null;
};
export type RetrievalRow = {
  id: string;
  principal: string;
  scope: string;
  references: string;
  past: number;
  expires: number;
};
export type AnswerRow = {
  id: string;
  client_id: string;
  subject: string;
  state: string;
  payload: string;
  result: string | null;
  references: string;
  error_code: string | null;
  expires: number;
};
export const nowISO = () => new Date().toISOString();
export const principalKey = (p: Principal) =>
  JSON.stringify([p.clientId, p.subject, p.grants.map(grantKey).sort(), p.credential_version]);

export class Store {
  db: Database;
  private owner: string | null = null;
  constructor(path: string) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = writerDatabase(path);
    try {
      const version =
        this.db.query<{ user_version: number }, []>("PRAGMA user_version").get()?.user_version ?? 0;
      if (version > 1) {
        this.db.close();
        throw new Error("Unsupported knowledge database version");
      }
      this.db.exec(`PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA busy_timeout=1000;
      CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, collection_id TEXT NOT NULL,
        project_id TEXT, region TEXT, source_kind TEXT NOT NULL, language TEXT NOT NULL,
        current_revision TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
        generation INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        owner_client TEXT NOT NULL, external_key TEXT
      );
      CREATE UNIQUE INDEX IF NOT EXISTS source_external ON sources(owner_client,external_key) WHERE deleted=0 AND external_key IS NOT NULL;
      CREATE TABLE IF NOT EXISTS revisions (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
        filename TEXT NOT NULL, suffix TEXT NOT NULL, sha256 TEXT NOT NULL,
        evidence_revision TEXT, state TEXT NOT NULL DEFAULT 'queued',
        fts_ready INTEGER NOT NULL DEFAULT 0, semantic_ready INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, indexed_at TEXT, warnings TEXT NOT NULL DEFAULT '[]'
      );
      CREATE TABLE IF NOT EXISTS contexts (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
        evidence_revision TEXT NOT NULL, body TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS chunks (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_revision TEXT NOT NULL,
        evidence_revision TEXT NOT NULL, context_id TEXT NOT NULL,
        text TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL, vector TEXT
      );
      CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_id,source_revision);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED,text,tokenize='trigram');
      CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY,vector TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, source_id TEXT, source_revision TEXT, kind TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued',
        run INTEGER NOT NULL DEFAULT 0, lease_until INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created_at);
      CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, hash TEXT NOT NULL, result TEXT NOT NULL, expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS retrievals (id TEXT PRIMARY KEY,principal TEXT NOT NULL,scope TEXT NOT NULL,
        "references" TEXT NOT NULL,past INTEGER NOT NULL,expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS answers (id TEXT PRIMARY KEY,client_id TEXT NOT NULL,subject TEXT NOT NULL,
        state TEXT NOT NULL,payload TEXT NOT NULL,result TEXT,"references" TEXT NOT NULL DEFAULT '[]',
        error_code TEXT,expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS api_lease (id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT NOT NULL,expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS ocr_operations (
        source_id TEXT NOT NULL, source_revision TEXT NOT NULL, key TEXT NOT NULL,
        body TEXT NOT NULL, PRIMARY KEY(source_revision,key)
      );
      CREATE TABLE IF NOT EXISTS ocr_budget (month TEXT PRIMARY KEY,submissions INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS viewer_artifacts (
        source_revision TEXT PRIMARY KEY REFERENCES revisions(id) ON DELETE CASCADE,
        evidence_revision TEXT NOT NULL,job_id TEXT NOT NULL,run INTEGER NOT NULL);
      PRAGMA user_version=1;`);
    } catch (error) {
      this.db.close();
      throw error;
    }
  }
  tx<T>(fn: () => T): T {
    return this.db
      .transaction(() => {
        this.assertLeader();
        return fn();
      })
      .immediate();
  }
  acquireLease() {
    const owner = randomUUID();
    this.tx(() => {
      const row = this.db
        .query<{ expires: number }, []>("SELECT expires FROM api_lease WHERE id=1")
        .get();
      if (row && row.expires > Date.now())
        throw new ApiError(
          503,
          "api_already_running",
          "Another API owns this database; retry after at most 30 seconds",
        );
      this.db.run("INSERT OR REPLACE INTO api_lease VALUES(1,?,?)", [owner, Date.now() + 30000]);
    });
    this.owner = owner;
  }
  isLeader() {
    if (!this.owner) return true;
    const row = this.db
      .query<{ owner: string; expires: number }, []>("SELECT * FROM api_lease WHERE id=1")
      .get();
    return !!row && row.owner === this.owner && row.expires > Date.now();
  }
  assertLeader() {
    if (!this.isLeader()) throw new ApiError(503, "api_lease_lost");
  }
  renewLease() {
    this.assertLeader();
    this.db.run("UPDATE api_lease SET expires=? WHERE id=1 AND owner=?", [
      Date.now() + 30000,
      this.owner,
    ]);
  }
  releaseLease() {
    this.db.run("DELETE FROM api_lease WHERE id=1 AND owner=?", [this.owner]);
  }
  source(id: string) {
    return this.db.query<SourceRow, string>("SELECT * FROM sources WHERE id=?").get(id);
  }
  revision(id: string) {
    return this.db.query<RevisionRow, string>("SELECT * FROM revisions WHERE id=?").get(id);
  }
  job(id: string) {
    return this.db.query<JobRow, string>("SELECT * FROM jobs WHERE id=?").get(id);
  }
  viewerArtifact(revision: string) {
    return this.db
      .query<{ evidence_revision: string; job_id: string; run: number }, string>(
        "SELECT * FROM viewer_artifacts WHERE source_revision=?",
      )
      .get(revision);
  }
  getSource(id: string, p: Principal): SourceRow {
    const source = this.source(id);
    if (!source || source.deleted || !authorized(p, source))
      throw new ApiError(404, "source_not_found");
    return source;
  }
  etag(s: SourceRow) {
    return `"${s.id}:${s.generation}"`;
  }
  match(s: SourceRow, value: string | null) {
    if (!value) throw new ApiError(428, "precondition_required");
    if (value !== this.etag(s)) throw new ApiError(412, "precondition_failed");
  }
  outstanding(kind: string) {
    return (
      this.db
        .query<{ n: number }, string>(
          "SELECT count(*) n FROM jobs WHERE kind=? AND state IN ('queued','running')",
        )
        .get(kind)?.n ?? 0
    );
  }
  enqueue(
    kind: JobRow["kind"],
    source: string | null,
    revision: string | null,
    payload: unknown,
    jobId = randomUUID(),
  ) {
    const time = nowISO();
    this.db.run(
      "INSERT INTO jobs(id,source_id,source_revision,kind,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
      [jobId, source, revision, kind, JSON.stringify(payload), time, time],
    );
    return jobId;
  }
  replay(key: string, hash: string): Record<string, string> | null {
    const row = this.db
      .query<{ hash: string; result: string; expires: number }, string>(
        "SELECT * FROM idempotency WHERE key=?",
      )
      .get(key);
    if (!row || row.expires < Date.now()) return null;
    if (row.hash !== hash) throw new ApiError(409, "idempotency_conflict");
    return JSON.parse(row.result);
  }
  remember(key: string, hash: string, result: unknown) {
    this.db.run("INSERT OR REPLACE INTO idempotency VALUES(?,?,?,?)", [
      key,
      hash,
      JSON.stringify(result),
      Date.now() + 7 * 86400000,
    ]);
  }
  register(
    p: Principal,
    m: Metadata,
    filename: string,
    suffix: string,
    hash: string,
    sourceId: string,
    revisionId: string,
    existing?: SourceRow,
    etag?: string | null,
    extraction: ProfileSelection | { profile: "local-v1" } = { profile: "local-v1" },
  ) {
    return this.tx(() => {
      if (existing) {
        this.match(this.getSource(existing.id, p), etag ?? null);
        this.db.run("UPDATE sources SET current_revision=?,generation=generation+1 WHERE id=?", [
          revisionId,
          sourceId,
        ]);
        this.db.run(
          "UPDATE jobs SET state='superseded',lease_until=0 WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
          [sourceId],
        );
        this.invalidateAnswers(sourceId);
      } else {
        if (
          m.external_key &&
          this.db
            .query("SELECT id FROM sources WHERE owner_client=? AND external_key=? AND deleted=0")
            .get(p.clientId, m.external_key)
        )
          throw new ApiError(409, "external_key_conflict");
        this.db.run("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,0,1,?,?,?)", [
          sourceId,
          m.title,
          m.collection_id,
          m.project_id ?? null,
          m.region ?? null,
          m.source_kind,
          m.language,
          revisionId,
          nowISO(),
          p.clientId,
          m.external_key ?? null,
        ]);
      }
      if (this.outstanding("extract") >= 3)
        throw new ApiError(429, "queue_full", "Document queue is full", true);
      this.db.run(
        "INSERT INTO revisions(id,source_id,filename,suffix,sha256,created_at) VALUES(?,?,?,?,?,?)",
        [revisionId, sourceId, filename, suffix, hash, nowISO()],
      );
      const jobId = this.enqueue("extract", sourceId, revisionId, extraction);
      checkpoint("register_before_commit");
      return { source_id: sourceId, source_revision: revisionId, job_id: jobId };
    });
  }
  invalidateAnswers(sourceId: string) {
    for (const row of this.db
      .query<AnswerRow, []>("SELECT * FROM answers WHERE result IS NOT NULL")
      .all()) {
      if ((JSON.parse(row.references) as Reference[]).some((r) => r.source_id === sourceId))
        this.db.run(
          "UPDATE answers SET state='failed',error_code='source_changed',result=NULL WHERE id=?",
          [row.id],
        );
    }
  }
  remove(id: string, p: Principal, etag: string | null) {
    return this.tx(() => {
      const source = this.getSource(id, p);
      this.match(source, etag);
      this.db.run(
        "UPDATE sources SET deleted=1,generation=generation+1,title='',external_key=NULL WHERE id=?",
        [id],
      );
      this.db.run(
        "UPDATE jobs SET state='superseded',lease_until=0 WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
        [id],
      );
      this.invalidateAnswers(id);
      return { source_id: id, job_id: this.enqueue("delete", id, null, {}) };
    });
  }
  claim(): JobRow | null {
    return this.tx(() => {
      this.db.run(
        "UPDATE answers SET state='failed',result=NULL,error_code='interrupted' WHERE id IN (SELECT id FROM jobs WHERE kind='answer' AND state='running' AND lease_until<?)",
        [Date.now()],
      );
      this.db.run(
        "UPDATE jobs SET state='failed',error_code='interrupted',lease_until=0 WHERE kind='answer' AND state='running' AND lease_until<?",
        [Date.now()],
      );
      this.db.run(
        "UPDATE jobs SET state='queued',lease_until=0 WHERE kind!='answer' AND state='running' AND lease_until<?",
        [Date.now()],
      );
      const row = this.db
        .query<JobRow, []>(
          "SELECT * FROM jobs WHERE state='queued' ORDER BY CASE WHEN kind='delete' THEN 0 ELSE 1 END,created_at,id LIMIT 1",
        )
        .get();
      if (!row) return null;
      this.db.run(
        "UPDATE jobs SET state='running',run=run+1,lease_until=?,updated_at=? WHERE id=?",
        [Date.now() + 60000, nowISO(), row.id],
      );
      return this.job(row.id);
    });
  }
  live(job: JobRow) {
    if (!this.isLeader()) return false;
    const current = this.job(job.id);
    if (!current || current.state !== "running" || current.run !== job.run) return false;
    if (job.kind === "extract") {
      const source = job.source_id && this.source(job.source_id);
      return !!source && !source.deleted && source.current_revision === job.source_revision;
    }
    return true;
  }
  heartbeat(job: JobRow) {
    if (!this.isLeader()) return;
    this.db.run("UPDATE jobs SET lease_until=? WHERE id=? AND run=? AND state='running'", [
      Date.now() + 60000,
      job.id,
      job.run,
    ]);
  }
  stage(job: JobRow, stage: string) {
    this.db.run("UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND run=? AND state='running'", [
      stage,
      nowISO(),
      job.id,
      job.run,
    ]);
  }
  finish(job: JobRow, state: string, error: string | null = null) {
    this.db.run(
      "UPDATE jobs SET state=?,error_code=?,lease_until=0,updated_at=? WHERE id=? AND run=? AND state='running'",
      [state, error, nowISO(), job.id, job.run],
    );
  }
  publish(job: JobRow, result: Extraction): string | null {
    return this.tx(() => {
      if (!this.live(job)) return null;
      const revision = this.revision(job.source_revision ?? "");
      if (
        !revision ||
        result.source_id !== job.source_id ||
        result.source_revision !== revision.id ||
        result.job_id !== job.id ||
        result.run !== job.run ||
        result.source_sha256 !== revision.sha256 ||
        result.profile !== (JSON.parse(job.payload).profile ?? "local-v1")
      )
        throw new ApiError(502, "extraction_mismatch");
      const evidence = randomUUID();
      // Re-extraction switches the published evidence only inside this successful transaction.
      this.db.run(
        "DELETE FROM chunk_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_revision=?)",
        [revision.id],
      );
      this.db.run("DELETE FROM chunks WHERE source_revision=?", [revision.id]);
      this.db.run("DELETE FROM contexts WHERE source_revision=?", [revision.id]);
      const contexts = new Map<string, string>();
      for (const item of result.contexts) {
        if (item.source_sha256 !== revision.sha256 || contexts.has(item.id))
          throw new ApiError(502, "invalid_extraction");
        const cid = randomUUID();
        contexts.set(item.id, cid);
        this.db.run("INSERT INTO contexts VALUES(?,?,?,?,?)", [
          cid,
          job.source_id,
          revision.id,
          evidence,
          JSON.stringify({ ...item, id: cid }),
        ]);
      }
      for (const item of result.chunks) {
        const parent = item.parent_id && contexts.get(item.parent_id);
        if (!parent || item.source_sha256 !== revision.sha256)
          throw new ApiError(502, "invalid_extraction");
        const cid = randomUUID();
        this.db.run("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,NULL)", [
          cid,
          job.source_id,
          revision.id,
          evidence,
          parent,
          item.text,
          JSON.stringify({ ...item, id: cid, parent_id: parent }),
          Bun.CryptoHasher.hash("sha256", item.text, "hex"),
        ]);
        this.db.run("INSERT INTO chunk_fts(chunk_id,text) VALUES(?,?)", [
          cid,
          item.text.normalize("NFKC"),
        ]);
      }
      this.db.run(
        "UPDATE revisions SET evidence_revision=?,state='indexing',fts_ready=1,indexed_at=?,warnings=? WHERE id=?",
        [evidence, nowISO(), JSON.stringify(result.warnings), revision.id],
      );
      this.db.run("DELETE FROM viewer_artifacts WHERE source_revision=?", [revision.id]);
      if (result.viewer_ready)
        this.db.run("INSERT INTO viewer_artifacts VALUES(?,?,?,?)", [
          revision.id,
          evidence,
          job.id,
          job.run,
        ]);
      checkpoint("api_publish_before_commit");
      return evidence;
    });
  }
  scopedRows(p: Principal, input: SearchInput, textOnly = false): Iterable<ChunkRow> {
    const grants = narrow(p, input.scope);
    const params: (string | number)[] = [];
    const conditions = grants.map((g) => {
      params.push(g.collection_id, g.project_id ?? "", g.region ?? "");
      return "(s.collection_id=? AND coalesce(s.project_id,'')=? AND coalesce(s.region,'')=?)";
    });
    let where = `s.deleted=0 AND r.fts_ready=1 AND (${conditions.join(" OR ")})`;
    if (!input.filter.include_past_revisions) where += " AND s.current_revision=r.id";
    where += " AND c.evidence_revision=r.evidence_revision";
    for (const [field, values] of [
      ["s.source_kind", input.filter.source_kinds],
      ["s.language", input.filter.languages],
    ] as const) {
      if (values?.length) {
        where += ` AND ${field} IN (${values.map(() => "?").join(",")})`;
        params.push(...values);
      }
    }
    if (textOnly) {
      const tokens = queryTokens(input.query);
      const long = tokens.filter((t) => [...t].length >= 3);
      const short = tokens.filter((t) => [...t].length < 3);
      // Trigram cannot match one/two-character words. Yield scoped rows for
      // normalized JS matching so Unicode case/width is consistent, and the
      // retrieval loop can check its deadline even on nonmatching rows.
      if (!short.length && long.length) {
        where += " AND c.id IN (SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ?)";
        params.push(long.map((t) => `"${t.replaceAll('"', '""')}"`).join(" OR "));
      }
    }
    return this.db
      .query<
        ChunkRow,
        (string | number)[]
      >(`SELECT s.*,c.id chunk_id,c.context_id,c.source_revision,c.evidence_revision,c.text,c.body,c.sha256,c.vector
      FROM chunks c JOIN revisions r ON r.id=c.source_revision JOIN sources s ON s.id=c.source_id WHERE ${where}`)
      .iterate(...params);
  }
  indexState(p: Principal, input: SearchInput) {
    const allowed = narrow(p, input.scope);
    const rows = this.db
      .query<
        SourceRow & RevisionRow,
        []
      >(`SELECT s.*,r.state,r.fts_ready,r.semantic_ready,r.indexed_at
      FROM sources s JOIN revisions r ON s.current_revision=r.id WHERE s.deleted=0`)
      .all()
      .filter((s) => allowed.some((g) => authorized({ ...p, grants: [g] }, s)))
      .filter(
        (s) =>
          !input.filter.source_kinds?.length || input.filter.source_kinds.includes(s.source_kind),
      )
      .filter(
        (s) => !input.filter.languages?.length || input.filter.languages.includes(s.language),
      );
    return {
      pending_sources: rows.filter((s) => !s.fts_ready && s.state !== "failed").length,
      failed_sources: rows.filter((s) => s.state === "failed" || s.state === "embedding_failed")
        .length,
      semantic_pending_sources: rows.filter((s) => s.fts_ready && !s.semantic_ready).length,
      snapshot_id: Bun.CryptoHasher.hash(
        "sha256",
        JSON.stringify(rows.map((s) => [s.id, s.current_revision, s.generation, s.indexed_at])),
        "hex",
      ),
      indexed_at:
        rows
          .map((s) => s.indexed_at)
          .filter(Boolean)
          .sort()
          .at(-1) ?? null,
    };
  }
  validReference(ref: Reference, p: Principal, past = false) {
    const source = this.getSource(ref.source_id, p);
    const rev = this.revision(ref.source_revision);
    if (
      (!past && source.current_revision !== ref.source_revision) ||
      !rev ||
      rev.source_id !== source.id ||
      rev.evidence_revision !== ref.evidence_revision
    )
      throw new ApiError(409, "source_changed");
    return source;
  }
  context(ref: Reference, p: Principal, past = false) {
    const source = this.validReference(ref, p, past);
    const row = this.db
      .query<{ body: string }, string[]>(
        "SELECT body FROM contexts WHERE id=? AND source_id=? AND source_revision=? AND evidence_revision=?",
      )
      .get(ref.context_id, ref.source_id, ref.source_revision, ref.evidence_revision);
    if (!row) throw new ApiError(404, "context_not_found");
    return {
      ...JSON.parse(row.body),
      ...ref,
      evidence_id: ref.context_id,
      is_current: source.current_revision === ref.source_revision,
    };
  }
  clearDeleted(id: string) {
    this.tx(() => {
      this.db.run(
        "DELETE FROM chunk_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id=?)",
        [id],
      );
      this.db.run("DELETE FROM chunks WHERE source_id=?", [id]);
      this.db.run("DELETE FROM contexts WHERE source_id=?", [id]);
      this.db.run("DELETE FROM revisions WHERE source_id=?", [id]);
      this.db.run("DELETE FROM ocr_operations WHERE source_id=?", [id]);
      this.db.run('DELETE FROM retrievals WHERE instr("references",?)>0', [id]);
      this.db.run(
        "DELETE FROM embeddings WHERE key NOT IN (SELECT json_extract(vector,'$.key') FROM chunks WHERE vector IS NOT NULL)",
      );
    });
  }
  ocrOperation(job: JobRow, key: string, record?: OcrRecord, version = 0, monthlyLimit = 10000) {
    return this.tx(() => {
      if (!this.live(job) || job.kind !== "extract") throw new ApiError(409, "ocr_cancelled");
      const row = this.db
        .query<{ body: string }, string[]>(
          "SELECT body FROM ocr_operations WHERE source_revision=? AND key=?",
        )
        .get(job.source_revision ?? "", key);
      const old = row ? (JSON.parse(row.body) as OcrRecord) : null;
      if (!record) return old;
      if ((old?.version ?? 0) !== version || record.key !== key)
        throw new ApiError(409, "ocr_state_conflict");
      const payload = JSON.parse(this.job(job.id)?.payload ?? "{}");
      const profile = payload.ocr_profile;
      const rev = this.revision(job.source_revision ?? "");
      if (
        !profile ||
        profile.provider !== "azure_read" ||
        !profile.enabled ||
        record.metadata.source_sha256 !== rev?.sha256 ||
        record.metadata.boundary !== job.source_id ||
        record.metadata.endpoint !== profile.endpoint ||
        record.metadata.model !== profile.model ||
        record.metadata.api_version !== profile.api_version
      )
        throw new ApiError(422, "ocr_profile_mismatch");
      const transitions: Record<string, string[]> = {
        prepared: ["prepared", "submitting"],
        submitting: ["submitted", "failed", "submission_unknown"],
        submitted: ["submitted", "polling", "completed"],
        polling: ["polling", "failed", "completed"],
        completed: ["completed", ...(payload.allow_ocr_resubmit ? ["prepared"] : [])],
        failed: ["prepared", "submitted"],
        submission_unknown: [
          "submission_unknown",
          ...(payload.allow_ocr_resubmit ? ["prepared"] : []),
        ],
      };
      // A new process cannot know whether a submitting operation reached Azure.
      if (
        old?.state === "submitting" &&
        record.state === "prepared" &&
        payload.allow_ocr_resubmit
      ) {
        transitions.submitting?.push("prepared");
      }
      if (
        (!old && record.state !== "prepared") ||
        (old && !transitions[old.state]?.includes(record.state))
      )
        throw new ApiError(409, "ocr_state_conflict");
      if (
        old &&
        ["submitting", "submission_unknown", "completed"].includes(old.state) &&
        record.state === "prepared"
      ) {
        // Consume the human acknowledgement before a new POST; a second crash needs a new one.
        this.db.run("UPDATE jobs SET payload=? WHERE id=? AND run=?", [
          JSON.stringify({ ...payload, allow_ocr_resubmit: false }),
          job.id,
          job.run,
        ]);
      }
      if (record.operation) {
        const op = new URL(record.operation),
          endpoint = new URL(profile.endpoint);
        if (
          op.protocol !== "https:" ||
          op.host !== endpoint.host ||
          op.username ||
          op.password ||
          op.hash ||
          !op.pathname.startsWith(
            "/documentintelligence/documentModels/prebuilt-read/analyzeResults/",
          ) ||
          op.searchParams.get("api-version") !== "2024-11-30"
        )
          throw new ApiError(422, "ocr_invalid_operation");
      }
      const submitting = record.state === "submitting" && old?.state !== "submitting";
      if (record.attempt !== (old?.attempt ?? 0) + (submitting ? 1 : 0))
        throw new ApiError(409, "ocr_state_conflict");
      if (submitting) {
        const month = nowISO().slice(0, 7);
        const used =
          this.db
            .query<{ submissions: number }, string>(
              "SELECT submissions FROM ocr_budget WHERE month=?",
            )
            .get(month)?.submissions ?? 0;
        const rows = this.db
          .query<{ body: string }, string>(
            "SELECT body FROM ocr_operations WHERE source_revision=?",
          )
          .all(job.source_revision ?? "");
        if (
          used >= monthlyLimit ||
          rows.reduce((n, r) => n + (JSON.parse(r.body).attempt ?? 0), 0) >= profile.max_submissions
        )
          throw new ApiError(429, "ocr_submission_limit");
        this.db.run(
          "INSERT INTO ocr_budget VALUES(?,1) ON CONFLICT(month) DO UPDATE SET submissions=submissions+1",
          [month],
        );
      }
      const saved = { ...record, version: version + 1 };
      this.db.run(
        "INSERT INTO ocr_operations VALUES(?,?,?,?) ON CONFLICT(source_revision,key) DO UPDATE SET body=excluded.body",
        [job.source_id, job.source_revision, key, JSON.stringify(saved)],
      );
      this.stage(job, `ocr_${record.state}`);
      return saved;
    });
  }
  prune() {
    this.db.run("DELETE FROM retrievals WHERE expires<?", [Date.now()]);
    this.db.run("DELETE FROM idempotency WHERE expires<?", [Date.now()]);
    this.db.run("DELETE FROM answers WHERE expires<?", [Date.now()]);
  }
}
