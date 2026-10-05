import { randomUUID } from "node:crypto";
import type { Clients } from "./auth";
import { refresh, requireAction } from "./auth";
import {
  ApiError,
  type Principal,
  queryTokens,
  type RagRecord,
  type Reference,
  type SearchInput,
} from "./contracts";
import type { EmbeddingProvider } from "./providers";
import { normalizedVectors } from "./providers";
import { type ChunkRow, principalKey, type RetrievalRow, type Store } from "./store";

export function locator(record: RagRecord) {
  const position = record.provenance;
  const ocr = record.ocr_evidence ?? [];
  if (record.kind === "slide")
    return {
      kind: "slide",
      slide_number: record.pages[0],
      provenance: position,
      ocr_evidence: ocr,
    };
  if (record.kind === "sheet")
    return {
      kind: "sheet",
      sheet_number: record.pages[0],
      sheet: record.unit,
      row_range: record.row_range,
      provenance: position,
      ocr_evidence: ocr,
    };
  if (record.pages.length)
    return {
      kind: "page",
      page_numbers: record.pages,
      page_number: record.pages[0],
      provenance: position,
      ocr_evidence: ocr,
    };
  return { kind: "section", headings: record.headings, provenance: position, ocr_evidence: ocr };
}
export const asReference = (c: ChunkRow): Reference => ({
  source_id: c.id,
  source_revision: c.source_revision,
  evidence_revision: c.evidence_revision,
  context_id: c.context_id,
});
export function referenceKey(r: Reference) {
  return JSON.stringify([r.source_id, r.source_revision, r.evidence_revision, r.context_id]);
}
export function embeddingKey(
  source: { collection_id: string; project_id: string | null; region: string | null },
  provider: EmbeddingProvider,
  hash: string,
) {
  return JSON.stringify([
    source.collection_id,
    source.project_id,
    source.region,
    provider.identity,
    provider.dimensions,
    "text-v1",
    hash,
  ]);
}
function textScore(text: string, query: string) {
  const normalized = text.normalize("NFKC").toLowerCase();
  return queryTokens(query)
    .map((t) => t.toLowerCase())
    .reduce((score, t) => {
      if (!t) return score;
      return (
        score + (normalized.includes(t) ? 1 + Math.min(4, normalized.split(t).length - 1) / 10 : 0)
      );
    }, 0);
}
export class Retrieval {
  constructor(
    readonly store: Store,
    readonly clients: Clients,
    readonly embedding?: EmbeddingProvider,
  ) {}
  async search(initial: Principal, input: SearchInput) {
    const p = refresh(initial, this.clients());
    requireAction(p, "read");
    const started = performance.now();
    const deadline = started + input.timeout_ms;
    const reasons: string[] = [];
    const textRanked: { c: ChunkRow; score: number }[] = [];
    const compare = (a: { c: ChunkRow; score: number }, b: { c: ChunkRow; score: number }) =>
      b.score - a.score || a.c.chunk_id.localeCompare(b.c.chunk_id);
    for (const c of this.store.scopedRows(p, input, true)) {
      if (performance.now() > deadline) {
        reasons.push("search_timeout");
        break;
      }
      const score = textScore(c.text, input.query);
      if (!score) continue;
      textRanked.push({ c, score });
      if (textRanked.length >= 712) {
        textRanked.sort(compare);
        textRanked.length = 200;
      }
    }
    const text = textRanked
      .sort(compare)
      .slice(0, 200)
      .map((v) => v.c);
    let semantic: ChunkRow[] = [];
    let effective: "text" | "semantic" | "hybrid" = input.mode;
    const index = this.store.indexState(p, input);
    if (index.pending_sources) reasons.push("index_pending");
    if (index.failed_sources) reasons.push("index_failed");
    if (input.mode !== "text") {
      if (!this.embedding) {
        effective = "text";
        reasons.push("embedding_not_configured");
      } else {
        const provider = this.embedding;
        try {
          const vectors = normalizedVectors(
            await provider.embed(
              [input.query],
              AbortSignal.timeout(Math.max(1, Math.ceil(deadline - performance.now()))),
            ),
            1,
            provider.dimensions,
          );
          const q = vectors[0];
          if (!q) throw new ApiError(502, "invalid_embedding");
          const candidates = this.store.scopedRows(p, input);
          const scored: { c: ChunkRow; score: number }[] = [];
          let missing = false;
          for (const c of candidates) {
            if (performance.now() > deadline) {
              reasons.push("search_timeout");
              break;
            }
            if (!c.vector) {
              missing = true;
              continue;
            }
            const saved = JSON.parse(c.vector) as { key: string; values: number[] };
            if (saved.key !== embeddingKey(c, provider, c.sha256)) {
              missing = true;
              continue;
            }
            const values = normalizedVectors([saved.values], 1, provider.dimensions)[0];
            if (!values) continue;
            scored.push({ c, score: q.reduce((sum, v, i) => sum + v * (values[i] ?? 0), 0) });
            if (scored.length >= 712) {
              scored.sort(compare);
              scored.length = 200;
            }
          }
          semantic = scored
            .sort(compare)
            .slice(0, 200)
            .map((s) => s.c);
          if (missing) {
            reasons.push("embedding_not_ready");
            effective = "hybrid";
          }
          if (!semantic.length && missing) effective = "text";
        } catch {
          effective = "text";
          reasons.push(performance.now() >= deadline ? "search_timeout" : "embedding_unavailable");
        }
      }
    }
    if (performance.now() > deadline && !reasons.includes("search_timeout"))
      reasons.push("search_timeout");
    const fused = new Map<string, { c: ChunkRow; score: number; matches: string[] }>();
    for (const [mode, rows] of [
      ["text", effective === "semantic" ? [] : text],
      ["semantic", effective === "text" ? [] : semantic],
    ] as const) {
      rows.forEach((c, i) => {
        const hit = fused.get(c.chunk_id) ?? { c, score: 0, matches: [] };
        hit.score += 1 / (60 + i + 1);
        hit.matches.push(mode);
        fused.set(c.chunk_id, hit);
      });
    }
    const current = refresh(p, this.clients());
    requireAction(current, "read");
    const seen = new Set<string>();
    const valid = [...fused.values()]
      .sort((a, b) => b.score - a.score || a.c.chunk_id.localeCompare(b.c.chunk_id))
      .filter((h) => {
        try {
          this.store.validReference(asReference(h.c), current, input.filter.include_past_revisions);
        } catch {
          reasons.push("source_changed");
          return false;
        }
        // Collapse repeated chunks only when their original parent and citation are identical.
        const key = JSON.stringify([h.c.id, h.c.source_revision, h.c.context_id, h.c.sha256]);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .slice(0, input.limit);
    if (!valid.length && reasons.includes("search_timeout"))
      throw new ApiError(504, "search_timeout", "Search timed out", true);
    if (!valid.length && index.pending_sources && !index.indexed_at)
      throw new ApiError(503, "index_not_ready", "Index is not ready", true);
    const retrievalId = randomUUID();
    this.store.db.run("INSERT INTO retrievals VALUES(?,?,?,?,?,?)", [
      retrievalId,
      principalKey(current),
      JSON.stringify(input.scope),
      JSON.stringify(valid.map((h) => asReference(h.c))),
      input.filter.include_past_revisions ? 1 : 0,
      Date.now() + 600000,
    ]);
    return {
      retrieval_id: retrievalId,
      status: reasons.length ? "partial" : "complete",
      requested_mode: input.mode,
      effective_mode: effective,
      degraded_reasons: [...new Set(reasons)],
      index_state: index,
      results: valid.map((h, i) => {
        const record = JSON.parse(h.c.body) as RagRecord;
        return {
          ...asReference(h.c),
          chunk_id: h.c.chunk_id,
          title: h.c.title,
          source_kind: h.c.source_kind,
          language: h.c.language,
          excerpt: h.c.text.slice(0, 1200),
          locator: locator(record),
          rank: i + 1,
          match_reasons: h.matches,
          is_current: h.c.current_revision === h.c.source_revision,
          source_url: `/api/v1/sources/${h.c.id}/content?revision=${h.c.source_revision}${h.c.current_revision !== h.c.source_revision ? "&include_past_revisions=true" : ""}`,
        };
      }),
    };
  }
  context(initial: Principal, retrievalId: string, refs: Reference[], maxChars: number) {
    const p = refresh(initial, this.clients());
    requireAction(p, "read");
    const snapshot = this.store.db
      .query<RetrievalRow, string>("SELECT * FROM retrievals WHERE id=?")
      .get(retrievalId);
    if (!snapshot || snapshot.expires < Date.now() || snapshot.principal !== principalKey(p))
      throw new ApiError(404, "retrieval_not_found");
    const allowed = new Set((JSON.parse(snapshot.references) as Reference[]).map(referenceKey));
    if (refs.some((r) => !allowed.has(referenceKey(r))))
      throw new ApiError(404, "context_not_found");
    const unique = [...new Map(refs.map((r) => [referenceKey(r), r])).values()];
    const contexts = unique.map((r) => this.store.context(r, p, !!snapshot.past));
    const chars = contexts.reduce((sum, c) => sum + (c.text as string).length, 0);
    if (chars > maxChars || Buffer.byteLength(JSON.stringify(contexts)) > 2 * 1024 * 1024) {
      throw new ApiError(
        413,
        "context_too_large",
        "Context exceeds response limit",
        false,
        contexts.map((c) => ({
          context_id: c.context_id,
          chars: (c.text as string).length,
          locator: locator(c),
        })),
      );
    }
    return {
      retrieval_id: retrievalId,
      contexts: contexts.map((c) => ({ ...c, locator: locator(c) })),
    };
  }
}
