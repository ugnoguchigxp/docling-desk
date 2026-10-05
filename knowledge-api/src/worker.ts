import { rm } from "node:fs/promises";
import { join } from "node:path";
import type { Clients } from "./auth";
import { refresh, requireAction } from "./auth";
import {
  type AnswerInput,
  ApiError,
  type Principal,
  type RagRecord,
  type Reference,
  searchSchema,
} from "./contracts";
import {
  type AnswerProvider,
  type EmbeddingProvider,
  type Evidence,
  type Extractor,
  type Generated,
  generatedSchema,
  normalizedVectors,
} from "./providers";
import { embeddingKey, locator, type Retrieval } from "./retrieval";
import { type AnswerRow, type ChunkRow, type JobRow, nowISO, type Store } from "./store";

export class Worker {
  private busy = false;
  private stopping = false;
  private aborter: AbortController | null = null;
  constructor(
    readonly store: Store,
    readonly artifactRoot: string,
    readonly clients: Clients,
    readonly extractor: Extractor,
    readonly retrieval: Retrieval,
    readonly embedding?: EmbeddingProvider,
    readonly answer?: AnswerProvider,
  ) {}
  async tick(): Promise<boolean> {
    if (this.busy || this.stopping) return false;
    const job = this.store.claim();
    if (!job) return false;
    this.busy = true;
    this.aborter = new AbortController();
    const signal = this.aborter.signal;
    const priorRevision = this.store.revision(job.source_revision ?? "");
    const heartbeat = setInterval(() => this.store.heartbeat(job), 15000);
    try {
      if (job.kind === "delete") await this.delete(job);
      else if (job.kind === "extract") await this.extract(job, signal);
      else await this.generate(job, signal);
      signal.throwIfAborted();
      if (this.store.live(job)) this.store.finish(job, "completed");
    } catch (error) {
      const code = signal.aborted
        ? "interrupted"
        : error instanceof ApiError
          ? error.code
          : "processing_failed";
      if (this.store.live(job)) {
        if (
          job.kind === "extract" &&
          !(
            JSON.parse(job.payload).reextract &&
            priorRevision?.fts_ready &&
            priorRevision.evidence_revision ===
              this.store.revision(job.source_revision ?? "")?.evidence_revision
          )
        )
          this.store.db.run(
            "UPDATE revisions SET state=CASE WHEN fts_ready=1 THEN 'embedding_failed' ELSE 'failed' END WHERE id=?",
            [job.source_revision],
          );
        if (
          job.kind === "extract" &&
          JSON.parse(job.payload).reextract &&
          priorRevision?.fts_ready &&
          priorRevision.evidence_revision ===
            this.store.revision(job.source_revision ?? "")?.evidence_revision
        )
          this.store.db.run("UPDATE revisions SET state=? WHERE id=?", [
            priorRevision.state === "indexing" ? "ready" : priorRevision.state,
            job.source_revision,
          ]);
        if (job.kind === "answer")
          this.store.db.run(
            "UPDATE answers SET state='failed',error_code=?,result=NULL WHERE id=?",
            [code, job.id],
          );
        this.store.finish(job, "failed", code);
      }
    } finally {
      clearInterval(heartbeat);
      // An old worker may return after a deletion or a new revision. It cannot publish.
      try {
        if (job.source_id && !this.store.live(job)) {
          const source = this.store.source(job.source_id);
          if (source?.deleted)
            await rm(join(this.artifactRoot, "sources", source.id), {
              recursive: true,
              force: true,
            });
        }
      } finally {
        this.busy = false;
        this.aborter = null;
      }
    }
    return true;
  }
  stop() {
    this.stopping = true;
    this.aborter?.abort();
  }
  private async delete(job: JobRow) {
    if (!job.source_id || !this.store.source(job.source_id)?.deleted)
      throw new ApiError(409, "source_changed");
    this.store.stage(job, "cleanup");
    await rm(join(this.artifactRoot, "sources", job.source_id), { recursive: true, force: true });
    this.store.clearDeleted(job.source_id);
  }
  private async extract(job: JobRow, signal: AbortSignal) {
    const revision = this.store.revision(job.source_revision ?? "");
    if (!revision || !this.store.live(job)) return;
    if (!revision.fts_ready || JSON.parse(job.payload).reextract) {
      this.store.stage(job, "extracting");
      const payload = JSON.parse(job.payload);
      const timeout =
        payload.ocr_profile?.provider === "azure_read"
          ? (payload.ocr_profile.document_timeout + 120) * 1000
          : 330000;
      const result = await this.extractor.extract(
        job,
        revision,
        AbortSignal.any([signal, AbortSignal.timeout(timeout)]),
      );
      signal.throwIfAborted();
      if (!this.store.publish(job, result)) return;
    }
    if (!this.store.live(job)) return;
    if (this.embedding) {
      this.store.stage(job, "embedding");
      const provider = this.embedding;
      const rows = this.store.db
        .query<ChunkRow, string>(`SELECT c.id chunk_id,c.*,s.collection_id,s.project_id,s.region
        FROM chunks c JOIN sources s ON s.id=c.source_id JOIN revisions r ON r.id=c.source_revision
        WHERE r.id=? AND c.evidence_revision=r.evidence_revision`)
        .all(revision.id);
      const missing = new Map<string, { text: string; rows: string[] }>();
      for (const row of rows) {
        const key = embeddingKey(row, provider, row.sha256);
        const cached = this.store.db
          .query<{ vector: string }, string>("SELECT vector FROM embeddings WHERE key=?")
          .get(key);
        if (cached) this.setVector(job, row.chunk_id, key, JSON.parse(cached.vector));
        else {
          const group = missing.get(key) ?? { text: row.text, rows: [] };
          group.rows.push(row.chunk_id);
          missing.set(key, group);
        }
      }
      const entries = [...missing.entries()];
      // Keep worst-case JSON vector responses below the 2 MiB gateway limit.
      const batchSize = Math.min(16, Math.max(1, Math.floor(65536 / provider.dimensions)));
      for (let start = 0; start < entries.length; start += batchSize) {
        if (!this.store.live(job)) return;
        const batch = entries.slice(start, start + batchSize);
        if (batch.some(([, g]) => Buffer.byteLength(g.text) > 24000))
          throw new ApiError(422, "embedding_input_too_large");
        const vectors = normalizedVectors(
          await provider.embed(
            batch.map(([, g]) => g.text),
            AbortSignal.any([signal, AbortSignal.timeout(90000)]),
          ),
          batch.length,
          provider.dimensions,
        );
        signal.throwIfAborted();
        this.store.tx(() => {
          if (!this.store.live(job)) return;
          batch.forEach(([key, group], i) => {
            const v = vectors[i];
            if (!v) throw new ApiError(502, "invalid_embedding");
            this.store.db.run("INSERT OR REPLACE INTO embeddings VALUES(?,?)", [
              key,
              JSON.stringify(v),
            ]);
            for (const row of group.rows) this.setVector(job, row, key, v);
          });
        });
      }
    }
    this.store.tx(() => {
      if (this.store.live(job))
        this.store.db.run("UPDATE revisions SET state='ready',semantic_ready=? WHERE id=?", [
          this.embedding ? 1 : 0,
          revision.id,
        ]);
    });
  }
  private setVector(job: JobRow, chunkId: string, key: string, values: number[]) {
    if (this.store.live(job))
      this.store.db.run("UPDATE chunks SET vector=? WHERE id=?", [
        JSON.stringify({ key, values }),
        chunkId,
      ]);
  }
  private async generate(job: JobRow, signal: AbortSignal) {
    if (!this.answer) throw new ApiError(503, "answer_not_configured");
    const payload = JSON.parse(job.payload) as { principal: Principal; input: AnswerInput };
    const principal = refresh(payload.principal, this.clients());
    requireAction(principal, "answer");
    const deadline = Date.now() + payload.input.timeout_ms;
    this.store.db.run("UPDATE answers SET state='running' WHERE id=?", [job.id]);
    this.store.stage(job, "retrieving");
    const result = await this.retrieval.search(
      principal,
      searchSchema.parse({
        query: payload.input.query,
        scope: payload.input.scope,
        mode: "hybrid",
        limit: 10,
        timeout_ms: Math.min(10000, payload.input.timeout_ms),
      }),
    );
    signal.throwIfAborted();
    const refs = result.results.map(asReferenceFromResult);
    this.store.db.run('UPDATE answers SET "references"=? WHERE id=?', [
      JSON.stringify(refs),
      job.id,
    ]);
    if (!refs.length) {
      this.store.db.run("UPDATE answers SET state='insufficient_evidence' WHERE id=?", [job.id]);
      return;
    }
    const contexts = this.retrieval.context(principal, result.retrieval_id, refs, 80000).contexts;
    const evidence: Evidence[] = contexts.map((c) => ({
      evidence_id: c.evidence_id,
      text: c.text,
      source_id: c.source_id,
      source_revision: c.source_revision,
      evidence_revision: c.evidence_revision,
      locator: locator(c as RagRecord),
      tables: c.tables,
    }));
    if (Date.now() >= deadline) throw new ApiError(504, "answer_timeout");
    this.store.stage(job, "generating");
    const generationSignal = AbortSignal.any([signal, AbortSignal.timeout(deadline - Date.now())]);
    let generated: Generated;
    try {
      const result = await this.answer.generate(payload.input.query, evidence, generationSignal);
      generationSignal.throwIfAborted();
      const parsed = generatedSchema.safeParse(result);
      if (!parsed.success) throw new ApiError(502, "invalid_provider_response");
      generated = parsed.data;
    } catch (error) {
      if (!signal.aborted && (generationSignal.aborted || Date.now() >= deadline))
        throw new ApiError(504, "answer_timeout", "Answer timed out", true);
      throw error;
    }
    this.store.tx(() => {
      if (!this.store.live(job)) return;
      const current = refresh(principal, this.clients());
      requireAction(current, "answer");
      for (const ref of refs) this.store.validReference(ref, current);
      if (
        !generated.answer.trim() ||
        !generated.citation_ids.length ||
        generated.citation_ids.some((c) => !contexts.some((e) => e.evidence_id === c))
      )
        throw new ApiError(502, "invalid_citations");
      if (Date.now() > deadline) throw new ApiError(504, "answer_timeout");
      const saved = {
        ...generated,
        retrieval_id: result.retrieval_id,
        degraded_reasons: result.degraded_reasons,
        citations: contexts
          .filter((c) => generated.citation_ids.includes(c.evidence_id))
          .map((c) => ({
            evidence_id: c.evidence_id,
            ...asReferenceFromResult(c),
            locator: locator(c as RagRecord),
            source_url: `/api/v1/sources/${c.source_id}/content?revision=${c.source_revision}`,
          })),
        created_at: nowISO(),
      };
      this.store.db.run(
        "UPDATE answers SET state='completed',result=?,error_code=NULL WHERE id=?",
        [JSON.stringify(saved), job.id],
      );
    });
  }
}
function asReferenceFromResult(c: Reference): Reference {
  return {
    source_id: c.source_id,
    source_revision: c.source_revision,
    evidence_revision: c.evidence_revision,
    context_id: c.context_id,
  };
}
export function readAnswer(store: Store, id: string, p: Principal) {
  const row = store.db.query<AnswerRow, string>("SELECT * FROM answers WHERE id=?").get(id);
  if (!row || row.client_id !== p.clientId || row.subject !== p.subject || row.expires < Date.now())
    throw new ApiError(404, "answer_not_found");
  if (row.result) {
    for (const ref of JSON.parse(row.references) as Reference[]) {
      try {
        store.validReference(ref, p);
      } catch {
        throw new ApiError(409, "source_changed");
      }
    }
  }
  return {
    answer_id: id,
    state: row.state,
    error_code: row.error_code,
    result: row.result ? JSON.parse(row.result) : null,
  };
}
