/** Editor-neutral operations; the HTTP API remains the authorization boundary. */
import { randomUUID } from "node:crypto";
import type { UploadAdmission } from "../admission";
import type { ApiOptions } from "../app";
import { authorized, refresh, requireAction } from "../auth";
import {
  ApiError,
  MAX_JSON,
  type Metadata,
  metadataSchema,
  type Principal,
  parse,
} from "../contracts";
import { embeddingKey } from "../retrieval";
import { nowISO, type SourceRow, type Store } from "../store";
import { attach, attachment, attachmentBytes } from "./attachments";
import { readBody } from "./body";
import { save } from "./commit";
import type { Draft, DraftRow } from "./contract";
import { createDraft, updateDraft } from "./drafts";
import { markdownView } from "./html";
import { updateMetadata } from "./metadata";

/** Arguments of an operation function after its leading service parameter. */
type Rest<F> = F extends (service: DocumentEditing, ...args: infer A) => unknown ? A : never;

export class DocumentEditing {
  constructor(
    readonly options: ApiOptions,
    readonly admission: UploadAdmission,
  ) {}
  get store(): Store {
    return this.options.store;
  }
  principal(p: Principal, action: string) {
    const fresh = refresh(p, this.options.clients());
    requireAction(fresh, action);
    return fresh;
  }
  metadata(source: SourceRow): Metadata {
    return parse(metadataSchema, {
      title: source.title,
      collection_id: source.collection_id,
      ...(source.project_id ? { project_id: source.project_id } : {}),
      ...(source.region ? { region: source.region } : {}),
      source_kind: source.source_kind,
      language: source.language,
      ...(source.external_key ? { external_key: source.external_key } : {}),
    });
  }
  checkExternalKey(source: SourceRow, metadata: Metadata) {
    if (
      metadata.external_key &&
      this.store.db
        .query(
          "SELECT 1 FROM sources WHERE owner_client=? AND external_key=? AND deleted=0 AND id<>?",
        )
        .get(source.owner_client, metadata.external_key, source.id)
    )
      throw new ApiError(409, "external_key_conflict");
  }
  state(source: SourceRow) {
    const revision = this.store.revision(source.current_revision);
    const provider = this.options.embedding;
    return {
      saved: true as const,
      search: !revision?.fts_ready
        ? ((revision?.state === "failed" ? "failed" : "pending") as "failed" | "pending")
        : ("complete" as const),
      fts_ready: !!revision?.fts_ready,
      semantic_ready:
        !!revision?.semantic_ready &&
        !!this.options.embedding &&
        this.store.db
          .query<{ vector: string | null; sha256: string }, string>(
            "SELECT vector,sha256 FROM chunks WHERE source_revision=?",
          )
          .all(source.current_revision)
          .every((row) => {
            try {
              return (
                !!row.vector &&
                !!provider &&
                JSON.parse(row.vector).key === embeddingKey(source, provider, row.sha256)
              );
            } catch {
              return false;
            }
          }),
      semantic_configured: !!this.options.embedding,
      evidence_revision: revision?.evidence_revision ?? null,
      error_code: ["failed", "embedding_failed"].includes(revision?.state ?? "")
        ? (revision?.state ?? null)
        : null,
    };
  }
  document(source: SourceRow) {
    return {
      source_id: source.id,
      source_revision: source.current_revision,
      etag: this.store.etag(source),
      sha256: this.store.revision(source.current_revision)?.sha256 ?? "",
      metadata: this.metadata(source),
      content_kind: [".md", ".markdown"].includes(
        this.store.revision(source.current_revision)?.suffix ?? "",
      )
        ? ("original_markdown" as const)
        : ("extracted_text" as const),
      status: this.state(source),
    };
  }
  draft(p: Principal, draftId: string): Draft {
    const row = this.store.db
      .query<DraftRow, string>("SELECT * FROM drafts WHERE id=?")
      .get(draftId);
    if (!row) throw new ApiError(404, "draft_not_found");
    const metadata = parse(metadataSchema, JSON.parse(row.metadata));
    const source = this.store.source(row.source_id);
    if (source) this.store.getSource(source.id, p);
    else if (!authorized(p, metadata)) throw new ApiError(404, "draft_not_found");
    return {
      draft_id: row.id,
      source_id: row.source_id,
      base_revision: row.base_revision,
      base_etag: row.base_etag,
      body: typeof row.body === "string" ? row.body : Buffer.from(row.body).toString("utf8"),
      metadata,
      attachments: JSON.parse(row.attachments),
      version: row.version,
      sha256: row.sha256,
      etag: `"${row.id}:${row.version}"`,
      state: row.state,
      committed_revision: row.committed_revision,
      created_by: row.created_by,
      updated_by: row.updated_by,
      created_at: row.created_at,
      updated_at: row.updated_at,
    };
  }
  match(draft: Draft, supplied: string | null) {
    if (!supplied) throw new ApiError(428, "precondition_required");
    if (supplied !== draft.etag) throw new ApiError(412, "precondition_failed");
    if (draft.state !== "draft") throw new ApiError(409, "draft_committed");
  }
  operation(p: Principal, kind: string, target: string | undefined, key: string | null) {
    if (!key || !/^[\x21-\x7e]{1,128}$/.test(key))
      throw new ApiError(428, "idempotency_key_required");
    return JSON.stringify([p.clientId, p.subject, `editing:${kind}:${target ?? ""}`, key]);
  }
  authorizeResult(p: Principal, result: Record<string, unknown>) {
    if (typeof result.draft_id === "string") this.draft(p, result.draft_id);
    else if (typeof result.source_id === "string") this.store.getSource(result.source_id, p);
  }
  replay(p: Principal, operation: string, requestHash: string) {
    const result = this.store.replay(operation, requestHash);
    if (result) this.authorizeResult(p, result);
    return result;
  }
  remember(
    p: Principal,
    operation: string,
    requestHash: string,
    kind: string,
    target: string | undefined,
    result: Record<string, unknown>,
    base: unknown,
  ) {
    // Check both the direct response and the larger operation-lookup envelope
    // inside the caller's transaction, before committing any mutation.
    if (Buffer.byteLength(JSON.stringify({ request_id: randomUUID(), result })) > MAX_JSON)
      throw new ApiError(413, "response_too_large");
    this.store.remember(operation, requestHash, result);
    this.store.db.run("INSERT INTO document_edits VALUES(?,?,?,?,?,?,?,?)", [
      randomUUID(),
      p.clientId,
      p.subject,
      kind,
      target ?? null,
      JSON.stringify(base),
      JSON.stringify(Object.fromEntries(Object.entries(result).filter(([key]) => key !== "body"))),
      nowISO(),
    ]);
    return result;
  }

  // Operations live in sibling modules; the service keeps its original public surface.
  attachment(...args: Rest<typeof attachment>) {
    return attachment(this, ...args);
  }
  attachmentBytes(...args: Rest<typeof attachmentBytes>) {
    return attachmentBytes(this, ...args);
  }
  body(...args: Rest<typeof readBody>) {
    return readBody(this, ...args);
  }
  markdownView(...args: Rest<typeof markdownView>) {
    return markdownView(this, ...args);
  }
  createDraft(...args: Rest<typeof createDraft>) {
    return createDraft(this, ...args);
  }
  updateDraft(...args: Rest<typeof updateDraft>) {
    return updateDraft(this, ...args);
  }
  save(...args: Rest<typeof save>) {
    return save(this, ...args);
  }
  attach(...args: Rest<typeof attach>) {
    return attach(this, ...args);
  }
  updateMetadata(...args: Rest<typeof updateMetadata>) {
    return updateMetadata(this, ...args);
  }
}
