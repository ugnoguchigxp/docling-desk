/** Editor-neutral operations; the HTTP API remains the authorization boundary. */
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import type { Hono } from "hono";
import { z } from "zod";
import type { UploadAdmission } from "./admission";
import type { ApiOptions, Env } from "./app";
import { authorized, refresh, requireAction } from "./auth";
import {
  ApiError,
  grantSchema,
  id,
  MAX_FILE,
  MAX_JSON,
  type Metadata,
  metadataSchema,
  type Principal,
  parse,
} from "./contracts";
import { checkpoint } from "./quality";
import { embeddingKey } from "./retrieval";
import { nowISO, type SourceRow, type Store } from "./store";

export const documentListParameters = {
  offset: z.number().int().min(0).max(1000000).default(0),
  limit: z.number().int().min(1).max(50).default(20),
  source_id: id.optional(),
};

const text = z.string().refine((s) => Buffer.from(s).toString("utf8") === s, "Invalid Unicode");
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const etag = z.string().min(1).max(256);
const attachmentIds = z
  .array(id)
  .max(100)
  .refine((ids) => new Set(ids).size === ids.length, "Duplicate attachments");
const range = z
  .object({
    start: z.number().int().nonnegative(),
    end: z.number().int().nonnegative(),
    expected: text,
    replacement: text,
  })
  .strict();
const match = z.object({ expected: text.min(1), replacement: text }).strict();
export const createMarkdownSchema = z.object({ body: text, metadata: metadataSchema }).strict();
export const createDraftSchema = z
  .object({
    source_id: id.optional(),
    source_revision: id.optional(),
    source_etag: etag.optional(),
    body: text.optional(),
    metadata: metadataSchema.optional(),
  })
  .strict();
export const updateDraftSchema = z
  .object({
    body: text,
    metadata: metadataSchema.optional(),
    attachments: attachmentIds.optional(),
  })
  .strict();
export const patchDraftSchema = z
  .object({
    base_sha256: hash,
    edits: z
      .array(z.union([range, match]))
      .min(1)
      .max(100),
  })
  .strict();
export const commitDraftSchema = z.object({ source_etag: etag.nullable() }).strict();
export const lookupSchema = z
  .object({
    operation: z.enum(["create", "draft", "update", "edit", "commit", "metadata", "attachment"]),
    target_id: id.optional(),
    key: z.string().min(1).max(128),
  })
  .strict();
export const attachSchema = z
  .object({
    filename: z.string().min(1).max(256),
    media_type: z.string().regex(/^[a-zA-Z0-9.+-]+\/[a-zA-Z0-9.+-]+$/),
    body_base64: z.string().max(MAX_JSON),
  })
  .strict();
const statusSchema = z.object({
  saved: z.literal(true),
  search: z.enum(["pending", "complete", "failed"]),
  fts_ready: z.boolean(),
  semantic_ready: z.boolean(),
  semantic_configured: z.boolean(),
  evidence_revision: id.nullable(),
  error_code: z.string().nullable(),
});
const savedSchema = z.object({
  source_id: id,
  source_revision: id,
  source_etag: etag,
  sha256: hash,
  job_id: id,
  operation_id: z.string(),
  draft_id: id.optional(),
  draft_version: z.number().int().optional(),
  status: statusSchema,
});
const draftSchema = z.object({
  draft_id: id,
  source_id: id,
  base_revision: id.nullable(),
  base_etag: etag.nullable(),
  body: text,
  metadata: metadataSchema,
  attachments: attachmentIds,
  version: z.number().int().positive(),
  etag,
  sha256: hash,
  state: z.enum(["draft", "committed"]),
  committed_revision: id.nullable(),
  created_by: z.string(),
  updated_by: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
const attachmentSchema = z.object({
  attachment_id: id,
  source_id: id,
  draft_id: id,
  filename: z.string(),
  media_type: z.string(),
  size: z.number().int(),
  sha256: hash,
  reference: z.string(),
  etag: etag.optional(),
});
const documentSchema = z.object({
  source_id: id,
  source_revision: id,
  etag,
  sha256: hash,
  metadata: metadataSchema,
  content_kind: z.enum(["original_markdown", "extracted_text"]),
  status: statusSchema,
});
const envelope = <T extends z.ZodType>(schema: T) =>
  z.object({ request_id: id }).extend(schema instanceof z.ZodObject ? schema.shape : {});
export const EDITING_ENDPOINTS = [
  {
    method: "get",
    path: "/api/v1/scopes",
    response: z.object({
      request_id: id,
      scopes: z.array(grantSchema),
      actions: z.array(z.string()),
    }),
  },
  {
    method: "get",
    path: "/api/v1/sources",
    response: z.object({
      request_id: id,
      documents: z.array(documentSchema),
      next_offset: z.number().nullable(),
    }),
  },
  {
    method: "get",
    path: "/api/v1/sources/{source_id}/body",
    response: envelope(
      documentSchema.extend({
        body: text,
        content_kind: z.enum(["original_markdown", "extracted_text"]),
        evidence_revision: id.nullable(),
      }),
    ),
  },
  {
    method: "post",
    path: "/api/v1/markdown-sources",
    input: createMarkdownSchema,
    response: envelope(savedSchema),
    write: true,
  },
  {
    method: "patch",
    path: "/api/v1/sources/{source_id}/metadata",
    input: metadataSchema,
    response: envelope(documentSchema),
    write: true,
    conditional: true,
  },
  {
    method: "post",
    path: "/api/v1/drafts",
    input: createDraftSchema,
    response: envelope(draftSchema),
    write: true,
  },
  {
    method: "get",
    path: "/api/v1/drafts",
    response: z.object({
      request_id: id,
      drafts: z.array(draftSchema.omit({ body: true })),
      next_offset: z.number().nullable(),
    }),
  },
  { method: "get", path: "/api/v1/drafts/{draft_id}", response: envelope(draftSchema) },
  {
    method: "put",
    path: "/api/v1/drafts/{draft_id}",
    input: updateDraftSchema,
    response: envelope(draftSchema),
    write: true,
    conditional: true,
  },
  {
    method: "patch",
    path: "/api/v1/drafts/{draft_id}",
    input: patchDraftSchema,
    response: envelope(draftSchema),
    write: true,
    conditional: true,
  },
  {
    method: "post",
    path: "/api/v1/drafts/{draft_id}/commit",
    input: commitDraftSchema,
    response: envelope(savedSchema),
    write: true,
    conditional: true,
  },
  {
    method: "post",
    path: "/api/v1/operations/lookup",
    input: lookupSchema,
    response: z.object({ request_id: id, result: z.record(z.string(), z.unknown()) }),
  },
  {
    method: "post",
    path: "/api/v1/drafts/{draft_id}/attachments",
    input: attachSchema,
    response: envelope(attachmentSchema),
    write: true,
    conditional: true,
  },
  {
    method: "get",
    path: "/api/v1/drafts/{draft_id}/attachments",
    response: z.object({ request_id: id, attachments: z.array(attachmentSchema) }),
  },
  {
    method: "get",
    path: "/api/v1/sources/{source_id}/attachments",
    response: z.object({ request_id: id, attachments: z.array(attachmentSchema) }),
  },
  {
    method: "get",
    path: "/api/v1/drafts/{draft_id}/attachments/{attachment_id}",
    response: envelope(attachmentSchema.extend({ body_base64: z.string() })),
  },
  {
    method: "get",
    path: "/api/v1/sources/{source_id}/attachments/{attachment_id}/content",
    response: z.unknown(),
  },
  {
    method: "get",
    path: "/api/v1/sources/{source_id}/attachments/{attachment_id}",
    response: envelope(attachmentSchema.extend({ body_base64: z.string() })),
  },
] as const;
export type Draft = z.infer<typeof draftSchema>;
type DraftRow = {
  id: string;
  source_id: string;
  base_revision: string | null;
  base_etag: string | null;
  body: Uint8Array | string;
  metadata: string;
  attachments: string;
  version: number;
  sha256: string;
  state: "draft" | "committed";
  committed_revision: string | null;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
};
type AttachmentRow = {
  id: string;
  source_id: string;
  draft_id: string;
  filename: string;
  media_type: string;
  size: number;
  sha256: string;
};
export const sha256 = (s: string | Uint8Array) => Bun.CryptoHasher.hash("sha256", s, "hex");
export function applyEdits(body: string, input: z.infer<typeof patchDraftSchema>): string {
  if (sha256(body) !== input.base_sha256) throw new ApiError(409, "body_changed");
  const bytes = Buffer.from(body);
  const edits = input.edits
    .map((edit) => {
      let start: number, end: number;
      if ("start" in edit) {
        start = edit.start;
        end = edit.end;
      } else {
        const needle = Buffer.from(edit.expected);
        start = bytes.indexOf(needle);
        if (start < 0) throw new ApiError(409, "edit_target_missing");
        if (bytes.indexOf(needle, start + 1) >= 0) throw new ApiError(409, "edit_target_ambiguous");
        end = start + needle.length;
      }
      if (
        start > end ||
        end > bytes.length ||
        (start < bytes.length && ((bytes[start] ?? 0) & 0xc0) === 0x80) ||
        (end < bytes.length && ((bytes[end] ?? 0) & 0xc0) === 0x80)
      )
        throw new ApiError(422, "invalid_edit_range");
      if (bytes.subarray(start, end).toString("utf8") !== edit.expected)
        throw new ApiError(409, "edit_target_mismatch");
      return { start, end, replacement: Buffer.from(edit.replacement) };
    })
    .sort((a, b) => a.start - b.start || a.end - b.end);
  for (let i = 1; i < edits.length; i++) {
    const current = edits[i],
      previous = edits[i - 1];
    if (!current || !previous || current.start < previous.end || current.start === previous.start)
      throw new ApiError(422, "overlapping_edits");
  }
  let result = bytes;
  for (const edit of edits.reverse())
    result = Buffer.concat([
      result.subarray(0, edit.start),
      edit.replacement,
      result.subarray(edit.end),
    ]);
  if (result.length > MAX_JSON) throw new ApiError(413, "body_too_large");
  return result.toString("utf8");
}
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
  attachment(p: Principal, sourceId: string, attachmentId: string) {
    const row = this.store.db
      .query<AttachmentRow, string>("SELECT * FROM attachments WHERE id=?")
      .get(attachmentId);
    if (!row || row.source_id !== sourceId) throw new ApiError(404, "attachment_not_found");
    const source = this.store.source(sourceId);
    if (source) this.store.getSource(sourceId, p);
    else this.draft(p, row.draft_id);
    return {
      attachment_id: row.id,
      source_id: row.source_id,
      draft_id: row.draft_id,
      filename: row.filename,
      media_type: row.media_type,
      size: row.size,
      sha256: row.sha256,
      reference: `knowledge-attachment:${row.id}`,
    };
  }
  async attachmentBytes(p: Principal, sourceId: string, attachmentId: string) {
    const attachment = this.attachment(p, sourceId, attachmentId);
    const bytes = await readFile(
      join(this.options.artifactRoot, "sources", sourceId, "attachments", attachmentId, "original"),
    );
    if (bytes.length !== attachment.size || sha256(bytes) !== attachment.sha256)
      throw new ApiError(500, "attachment_integrity_failed");
    this.attachment(this.principal(p, "read"), sourceId, attachmentId);
    return { attachment, bytes };
  }
  async body(p: Principal, sourceId: string, revisionId?: string) {
    requireAction(p, "read");
    const source = this.store.getSource(sourceId, p);
    if (revisionId && source.current_revision !== revisionId)
      throw new ApiError(409, "source_changed");
    const revision = this.store.revision(source.current_revision);
    if (!revision) throw new ApiError(404, "source_not_found");
    let body: string;
    const markdown = [".md", ".markdown"].includes(revision.suffix);
    if (markdown) {
      const bytes = await readFile(
        join(
          this.options.artifactRoot,
          "sources",
          sourceId,
          revision.id,
          `original${revision.suffix}`,
        ),
      );
      if (sha256(bytes) !== revision.sha256) throw new ApiError(500, "source_integrity_failed");
      body = bytes.toString("utf8");
      if (!Buffer.from(body).equals(bytes)) throw new ApiError(422, "invalid_utf8");
    } else {
      if (!revision.fts_ready) throw new ApiError(409, "body_not_ready");
      body = this.store.db
        .query<{ body: string }, string>(
          "SELECT body FROM contexts WHERE source_revision=? ORDER BY rowid",
        )
        .all(revision.id)
        .map((row) => JSON.parse(row.body).text)
        .join("\n\n");
    }
    const current = this.store.getSource(sourceId, this.principal(p, "read"));
    if (current.current_revision !== revision.id) throw new ApiError(409, "source_changed");
    const result = {
      ...this.document(current),
      body,
      content_kind: markdown ? ("original_markdown" as const) : ("extracted_text" as const),
      evidence_revision: revision.evidence_revision,
    };
    if (Buffer.byteLength(JSON.stringify(result)) > MAX_JSON - 256)
      throw new ApiError(413, "body_too_large");
    return result;
  }
  async markdownView(
    p: Principal,
    input: {
      source_id: string;
      source_revision: string;
      evidence_revision: string;
      resource: string;
      prefix: string;
      original_range?: { start: number; end: number; sha256: string };
    },
  ) {
    const data = await this.body(p, input.source_id, input.source_revision);
    if (data.evidence_revision !== input.evidence_revision)
      throw new ApiError(409, "evidence_changed");
    if (input.original_range && input.original_range.sha256 !== data.sha256)
      throw new ApiError(409, "source_changed");
    if (input.resource === "manifest")
      return {
        job: {
          id: data.source_id,
          filename: "document.md",
          state: "success",
          pages: 1,
          preview: "original.md",
          slide_layout: false,
        },
        kind: "document",
        units: 1,
      };
    const attachments = this.store.db
      .query<{ attachment_id: string }, string>(
        "SELECT attachment_id FROM revision_attachments WHERE revision_id=?",
      )
      .all(data.source_revision)
      .map((row) => this.attachment(p, data.source_id, row.attachment_id));
    if (input.resource.startsWith("attachment/")) {
      const aid = parse(id, input.resource.slice("attachment/".length));
      if (!attachments.some((a) => a.attachment_id === aid))
        throw new ApiError(404, "attachment_not_found");
      const { attachment, bytes } = await this.attachmentBytes(p, data.source_id, aid);
      if (bytes.length > 1024 * 1024) throw new ApiError(413, "attachment_too_large_for_json");
      return {
        media_type: ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
          attachment.media_type,
        )
          ? attachment.media_type
          : "application/octet-stream",
        body_base64: bytes.toString("base64"),
      };
    }
    const escapeHTML = (value: string) =>
      value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    if (input.resource.split("?")[0] !== "view/document")
      throw new ApiError(404, "viewer_resource_unavailable");
    let content = escapeHTML(data.body);
    if (input.original_range) {
      const r = input.original_range;
      applyEdits(data.body, {
        base_sha256: data.sha256,
        edits: [
          {
            start: r.start,
            end: r.end,
            expected: Buffer.from(data.body).subarray(r.start, r.end).toString("utf8"),
            replacement: "",
          },
        ],
      });
      const bytes = Buffer.from(data.body);
      content =
        escapeHTML(bytes.subarray(0, r.start).toString()) +
        '<mark id="citation">' +
        escapeHTML(bytes.subarray(r.start, r.end).toString()) +
        "</mark>" +
        escapeHTML(bytes.subarray(r.end).toString());
    }
    const attachmentHTML = attachments.length
      ? "<section><h2>添付ファイル</h2>" +
        attachments
          .map((a) => {
            const filename = escapeHTML(a.filename);
            const url = input.prefix + "attachment/" + a.attachment_id;
            if (a.size > 1024 * 1024)
              return "<p>" + filename + " — 1 MiBを超える添付はHTTP APIから取得してください。</p>";
            const image = ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
              a.media_type,
            );
            return (
              '<p><a href="' +
              url +
              '">' +
              filename +
              "</a></p>" +
              (image
                ? '<img src="' +
                  url +
                  '" alt="' +
                  filename +
                  '" style="max-width:100%;max-height:400px">'
                : "")
            );
          })
          .join("") +
        "</section>"
      : "";
    const html =
      '<!doctype html><html lang="ja"><meta charset="utf-8"><title>' +
      escapeHTML(data.metadata.title) +
      "</title><body><h1>" +
      escapeHTML(data.metadata.title) +
      '</h1><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' +
      content +
      "</pre>" +
      attachmentHTML +
      "</body></html>";
    return {
      media_type: "text/html; charset=utf-8",
      body_base64: Buffer.from(html).toString("base64"),
    };
  }
  async createDraft(
    initial: Principal,
    input: z.infer<typeof createDraftSchema>,
    key: string | null,
  ) {
    let p = this.principal(initial, "write");
    const operation = this.operation(p, "draft", input.source_id, key),
      requestHash = sha256(JSON.stringify(input));
    const replay = this.replay(p, operation, requestHash);
    if (replay) return replay;
    let source: SourceRow | undefined,
      body = input.body ?? "",
      metadata = input.metadata;
    if (input.source_id) {
      source = this.store.getSource(input.source_id, p);
      this.store.match(source, input.source_etag ?? null);
      if (source.current_revision !== input.source_revision)
        throw new ApiError(409, "source_changed");
      const r = this.store.revision(source.current_revision);
      if (!r || ![".md", ".markdown"].includes(r.suffix))
        throw new ApiError(422, "markdown_required");
      if (input.body === undefined)
        body = (await this.body(p, source.id, source.current_revision)).body;
      metadata ??= this.metadata(source);
    } else if (input.source_etag || input.source_revision) throw new ApiError(422, "invalid_base");
    if (!metadata || !authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
    p = this.principal(p, "write");
    return this.store.tx(() => {
      const repeated = this.replay(p, operation, requestHash);
      if (repeated) return repeated;
      if (source) this.store.match(this.store.getSource(source.id, p), input.source_etag ?? null);
      const draftId = randomUUID(),
        now = nowISO();
      this.store.db.run("INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        draftId,
        source?.id ?? randomUUID(),
        source?.current_revision ?? null,
        input.source_etag ?? null,
        Buffer.from(body),
        JSON.stringify(metadata),
        JSON.stringify(
          source
            ? this.store.db
                .query<{ attachment_id: string }, string>(
                  "SELECT attachment_id FROM revision_attachments WHERE revision_id=?",
                )
                .all(source.current_revision)
                .map((row) => row.attachment_id)
            : [],
        ),
        1,
        sha256(body),
        "draft",
        null,
        p.subject,
        p.subject,
        now,
        now,
      ]);
      const result = this.draft(p, draftId);
      return this.remember(p, operation, requestHash, "draft", source?.id, result, {
        source_revision: source?.current_revision ?? null,
      });
    });
  }
  updateDraft(
    p: Principal,
    draftId: string,
    input: z.infer<typeof updateDraftSchema> | z.infer<typeof patchDraftSchema>,
    supplied: string | null,
    key: string | null,
    partial = false,
  ) {
    p = this.principal(p, "write");
    const d = this.draft(p, draftId);
    const operation = this.operation(p, partial ? "edit" : "update", draftId, key),
      requestHash = sha256(JSON.stringify([input, supplied]));
    return this.store.tx(() => {
      const replay = this.replay(p, operation, requestHash);
      if (replay) return replay;
      this.match(d, supplied);
      const body = "edits" in input ? applyEdits(d.body, input) : input.body;
      const metadata = "metadata" in input && input.metadata ? input.metadata : d.metadata;
      if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
      const attachments =
        "attachments" in input && input.attachments ? input.attachments : d.attachments;
      for (const aid of attachments) this.attachment(p, d.source_id, aid);
      this.store.db.run(
        "UPDATE drafts SET body=?,metadata=?,attachments=?,version=version+1,sha256=?,updated_by=?,updated_at=? WHERE id=?",
        [
          Buffer.from(body),
          JSON.stringify(metadata),
          JSON.stringify(attachments),
          sha256(body),
          p.subject,
          nowISO(),
          draftId,
        ],
      );
      return this.remember(
        p,
        operation,
        requestHash,
        partial ? "edit" : "update",
        draftId,
        this.draft(p, draftId),
        { draft_version: d.version, source_revision: d.base_revision, sha256: d.sha256 },
      );
    });
  }
  async save(
    initial: Principal,
    body: string,
    metadata: Metadata,
    key: string | null,
    draftId?: string,
    supplied?: string | null,
    baseEtag?: string | null,
  ) {
    let p = this.principal(initial, "write");
    const d = draftId ? this.draft(p, draftId) : undefined;
    const operation = this.operation(p, d ? "commit" : "create", draftId, key),
      requestHash = sha256(JSON.stringify([body, metadata, supplied ?? null, baseEtag ?? null]));
    const replay = this.replay(p, operation, requestHash);
    if (replay) return replay;
    if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
    if (d) {
      this.match(d, supplied ?? null);
      if (baseEtag !== d.base_etag) throw new ApiError(412, "precondition_failed");
    }
    const existing = d?.base_revision ? this.store.getSource(d.source_id, p) : undefined;
    if (existing) this.store.match(existing, baseEtag ?? null);
    const sourceId = d?.source_id ?? randomUUID(),
      revisionId = randomUUID();
    // Reserve room for the complete body/status response after indexing as
    // well as the small save receipt. Saving an unreadable body is not success.
    const bodyResponse = {
      request_id: randomUUID(),
      source_id: sourceId,
      source_revision: revisionId,
      etag: `"${sourceId}:9007199254740991"`,
      sha256: sha256(body),
      body,
      metadata,
      content_kind: "original_markdown",
      evidence_revision: revisionId,
      status: {
        saved: true,
        search: "complete",
        fts_ready: true,
        semantic_ready: false,
        semantic_configured: true,
        evidence_revision: revisionId,
        error_code: "embedding_failed",
      },
    };
    if (Buffer.byteLength(JSON.stringify(bodyResponse)) > MAX_JSON - 256)
      throw new ApiError(413, "body_too_large");
    const folder = join(this.options.artifactRoot, "sources", sourceId, revisionId);
    const releaseSpace = await this.admission.reserve(Buffer.byteLength(body));
    let registered = false;
    try {
      await mkdir(folder, { recursive: true });
      const file = Bun.file(join(folder, "original.md"));
      await Bun.write(file, body);
      const fd = await import("node:fs/promises").then((fs) =>
        fs.open(join(folder, "original.md"), "r"),
      );
      await fd.sync();
      await fd.close();
      checkpoint("original_written");
      p = this.principal(p, "write");
      if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
      const result = this.store.tx(() => {
        const repeated = this.replay(p, operation, requestHash);
        if (repeated) return repeated;
        if (d) {
          this.match(this.draft(p, d.draft_id), supplied ?? null);
          if (existing) this.store.match(this.store.getSource(sourceId, p), baseEtag ?? null);
        }
        if (existing) this.checkExternalKey(existing, metadata);
        const saved = this.store.register(
          p,
          metadata,
          "document.md",
          ".md",
          sha256(body),
          sourceId,
          revisionId,
          existing,
          baseEtag ?? undefined,
          { profile: "local-v1" },
          false,
        );
        this.store.db.run(
          "UPDATE sources SET title=?,collection_id=?,project_id=?,region=?,source_kind=?,language=?,external_key=? WHERE id=?",
          [
            metadata.title,
            metadata.collection_id,
            metadata.project_id ?? null,
            metadata.region ?? null,
            metadata.source_kind,
            metadata.language,
            metadata.external_key ?? null,
            sourceId,
          ],
        );
        this.store.db.run("INSERT INTO revision_metadata VALUES(?,?)", [
          revisionId,
          JSON.stringify(metadata),
        ]);
        if (d) {
          for (const aid of d.attachments) {
            this.attachment(p, sourceId, aid);
            this.store.db.run("INSERT INTO revision_attachments VALUES(?,?)", [revisionId, aid]);
          }
          this.store.db.run(
            "UPDATE drafts SET state='committed',committed_revision=?,version=version+1,updated_by=?,updated_at=? WHERE id=?",
            [revisionId, p.subject, nowISO(), d.draft_id],
          );
        }
        const source = this.store.getSource(sourceId, p);
        const result = {
          ...saved,
          source_etag: this.store.etag(source),
          sha256: sha256(body),
          operation_id: operation,
          ...(d ? { draft_id: d.draft_id, draft_version: d.version } : {}),
          status: this.state(source),
        };
        this.remember(p, operation, requestHash, d ? "commit" : "create", draftId, result, {
          source_revision: d?.base_revision ?? null,
          draft_version: d?.version ?? null,
          sha256: d?.sha256 ?? null,
        });
        registered = true;
        return result;
      });
      checkpoint("editing_saved");
      return result;
    } finally {
      releaseSpace();
      if (!registered) await rm(folder, { recursive: true, force: true });
    }
  }
  async attach(
    initial: Principal,
    draftId: string,
    input: z.infer<typeof attachSchema>,
    supplied: string | null,
    key: string | null,
    binary?: Uint8Array,
  ) {
    let p = this.principal(initial, "write");
    const d = this.draft(p, draftId);
    const bytes = binary ? Buffer.from(binary) : Buffer.from(input.body_base64, "base64");
    if (!binary && bytes.toString("base64") !== input.body_base64)
      throw new ApiError(422, "invalid_base64");
    if (!bytes.length || bytes.length > MAX_FILE) throw new ApiError(413, "invalid_file_size");
    const operation = this.operation(p, "attachment", draftId, key),
      requestHash = sha256(
        JSON.stringify([input.filename, input.media_type, sha256(bytes), supplied]),
      );
    const replay = this.replay(p, operation, requestHash);
    if (replay) return replay;
    this.match(d, supplied);
    if (d.attachments.length >= 100) throw new ApiError(422, "attachment_limit_reached");
    const releaseSpace = await this.admission.reserve(bytes.length);
    const aid = randomUUID(),
      folder = join(this.options.artifactRoot, "sources", d.source_id, "attachments", aid);
    let registered = false;
    try {
      await mkdir(folder, { recursive: true });
      await Bun.write(join(folder, "original"), bytes);
      const fd = await import("node:fs/promises").then((fs) =>
        fs.open(join(folder, "original"), "r"),
      );
      try {
        await fd.sync();
      } finally {
        await fd.close();
      }
      checkpoint("attachment_written");
      p = this.principal(p, "write");
      const result = this.store.tx(() => {
        const repeated = this.replay(p, operation, requestHash);
        if (repeated) return repeated;
        this.match(this.draft(p, draftId), supplied);
        this.store.db.run("INSERT INTO attachments VALUES(?,?,?,?,?,?,?)", [
          aid,
          d.source_id,
          draftId,
          input.filename,
          input.media_type,
          bytes.length,
          sha256(bytes),
        ]);
        this.store.db.run(
          "UPDATE drafts SET attachments=?,version=version+1,updated_by=?,updated_at=? WHERE id=?",
          [JSON.stringify([...d.attachments, aid]), p.subject, nowISO(), draftId],
        );
        const result = {
          ...this.attachment(p, d.source_id, aid),
          etag: this.draft(p, draftId).etag,
        };
        this.remember(p, operation, requestHash, "attachment", draftId, result, {
          draft_version: d.version,
        });
        checkpoint("attachment_before_commit");
        return result;
      });
      registered = true;
      checkpoint("attachment_saved");
      return result;
    } finally {
      releaseSpace();
      if (!registered) await rm(folder, { recursive: true, force: true });
    }
  }
}
export function installEditing(
  app: Hono<Env>,
  options: ApiOptions,
  json: (request: Request) => Promise<unknown>,
  boundedBody: (request: Request, max: number) => Promise<Buffer>,
  admission: UploadAdmission,
) {
  const service = new DocumentEditing(options, admission),
    store = options.store;
  for (const endpoint of EDITING_ENDPOINTS) {
    const path = endpoint.path.replace(/\{(\w+)\}/g, ":$1");
    if (endpoint.method === "post" && endpoint.path.endsWith("/attachments")) {
      app.post(path, async (c, next) => {
        requireAction(c.get("principal"), "write");
        const release = admission.acquire();
        try {
          await next();
        } finally {
          release();
        }
      });
    }
    app.on(endpoint.method.toUpperCase(), path, async (c) => {
      const action = "write" in endpoint && endpoint.write ? "write" : "read";
      let p = service.principal(c.get("principal"), action);
      const readJSON = async () => {
        const input = await json(c.req.raw);
        p = service.principal(p, action);
        return input;
      };
      const sourceId = c.req.param("source_id") ?? "",
        draftId = c.req.param("draft_id") ?? "";
      const key = c.req.header("idempotency-key") ?? null,
        condition = c.req.header("if-match") ?? null;
      let result: Record<string, unknown>;
      if (endpoint.path === "/api/v1/scopes") result = { scopes: p.grants, actions: p.actions };
      else if (
        endpoint.method === "get" &&
        (endpoint.path === "/api/v1/sources" || endpoint.path === "/api/v1/drafts")
      ) {
        const query = c.req.query();
        const offset = parse(
          documentListParameters.offset,
          query.offset === undefined ? undefined : Number(query.offset),
        );
        const limit = parse(
          documentListParameters.limit,
          query.limit === undefined ? undefined : Number(query.limit),
        );
        const sourceFilter = c.req.query("source_id");
        if (sourceFilter) parse(documentListParameters.source_id, sourceFilter);
        const rows =
          endpoint.path === "/api/v1/sources"
            ? store.db
                .query<SourceRow, []>(
                  "SELECT * FROM sources WHERE deleted=0 ORDER BY created_at,id",
                )
                .all()
                .filter((s) => authorized(p, s))
                .map((s) => service.document(s))
            : store.db
                .query<{ id: string }, []>("SELECT id FROM drafts ORDER BY created_at,id")
                .all()
                .flatMap((row) => {
                  try {
                    const d = service.draft(p, row.id);
                    if (sourceFilter && d.source_id !== sourceFilter) return [];
                    const { body: _, ...info } = d;
                    return [info];
                  } catch (e) {
                    if (e instanceof ApiError && e.status === 404) return [];
                    throw e;
                  }
                });
        result = {
          [endpoint.path === "/api/v1/sources" ? "documents" : "drafts"]: rows.slice(
            offset,
            offset + limit,
          ),
          next_offset: rows.length > offset + limit ? offset + limit : null,
        };
      } else if (endpoint.path.endsWith("/body"))
        result = await service.body(p, parse(id, sourceId), c.req.query("revision"));
      else if (endpoint.path === "/api/v1/markdown-sources") {
        const input = parse(createMarkdownSchema, await readJSON());
        result = await service.save(p, input.body, input.metadata, key);
      } else if (endpoint.path === "/api/v1/drafts" && endpoint.method === "post")
        result = await service.createDraft(p, parse(createDraftSchema, await readJSON()), key);
      else if (endpoint.path === "/api/v1/drafts/{draft_id}") {
        parse(id, draftId);
        if (endpoint.method === "get") result = service.draft(p, draftId);
        else
          result = service.updateDraft(
            p,
            draftId,
            endpoint.method === "put"
              ? parse(updateDraftSchema, await readJSON())
              : parse(patchDraftSchema, await readJSON()),
            condition,
            key,
            endpoint.method === "patch",
          );
      } else if (endpoint.path.endsWith("/commit")) {
        const input = parse(commitDraftSchema, await readJSON());
        const d = service.draft(p, parse(id, draftId));
        result = await service.save(
          p,
          d.body,
          d.metadata,
          key,
          draftId,
          condition,
          input.source_etag,
        );
      } else if (endpoint.path.endsWith("/metadata")) {
        const metadata = parse(metadataSchema, await readJSON());
        const source = store.getSource(parse(id, sourceId), p);
        if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
        if (
          metadata.source_kind === "wiki" &&
          ![".md", ".markdown", ".txt", ".text"].includes(
            store.revision(source.current_revision)?.suffix ?? "",
          )
        )
          throw new ApiError(422, "wiki_text_required");
        const op = service.operation(p, "metadata", sourceId, key),
          h = sha256(JSON.stringify([metadata, condition]));
        result = store.tx(() => {
          const replay = service.replay(p, op, h);
          if (replay) return replay;
          store.match(source, condition);
          service.checkExternalKey(source, metadata);
          const old = service.metadata(source);
          store.db.run(
            "UPDATE sources SET title=?,collection_id=?,project_id=?,region=?,source_kind=?,language=?,external_key=?,generation=generation+1 WHERE id=?",
            [
              metadata.title,
              metadata.collection_id,
              metadata.project_id ?? null,
              metadata.region ?? null,
              metadata.source_kind,
              metadata.language,
              metadata.external_key ?? null,
              source.id,
            ],
          );
          if (
            !authorized(
              {
                ...p,
                grants: [
                  {
                    collection_id: old.collection_id,
                    project_id: old.project_id,
                    region: old.region,
                  },
                ],
              },
              metadata,
            )
          ) {
            store.db.run(
              "UPDATE jobs SET state='superseded',lease_until=0 WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
              [source.id],
            );
            store.db.run("UPDATE revisions SET semantic_ready=0 WHERE source_id=?", [source.id]);
            store.enqueue("extract", source.id, source.current_revision, { profile: "local-v1" });
          }
          store.invalidateAnswers(source.id);
          return service.remember(
            p,
            op,
            h,
            "metadata",
            sourceId,
            service.document(store.getSource(sourceId, p)),
            { source_revision: source.current_revision, etag: condition },
          );
        });
      } else if (endpoint.path === "/api/v1/operations/lookup") {
        const input = parse(lookupSchema, await readJSON());
        const op = service.operation(p, input.operation, input.target_id, input.key);
        const row = store.db
          .query<{ result: string }, string>(
            "SELECT result FROM idempotency WHERE key=? AND expires>strftime('%s','now')*1000",
          )
          .get(op);
        if (!row) throw new ApiError(404, "operation_unknown");
        const saved = JSON.parse(row.result);
        service.authorizeResult(p, saved);
        result = { result: saved };
      } else if (endpoint.path.endsWith("/attachments") && endpoint.method === "post") {
        parse(id, draftId);
        if (c.req.header("content-type")?.startsWith("multipart/form-data;")) {
          const raw = await boundedBody(c.req.raw, MAX_FILE + 1024 * 1024);
          const form = await new Request(c.req.url, {
            method: "POST",
            headers: c.req.raw.headers,
            body: new Uint8Array(raw),
          }).formData();
          const file = form.get("file");
          if (
            !(file instanceof File) ||
            [...form.keys()].some((k) => k !== "file") ||
            form.getAll("file").length !== 1
          )
            throw new ApiError(422, "invalid_multipart");
          const input = parse(attachSchema, {
            filename: file.name,
            media_type: file.type || "application/octet-stream",
            body_base64: "",
          });
          result = await service.attach(
            p,
            draftId,
            input,
            condition,
            key,
            new Uint8Array(await file.arrayBuffer()),
          );
        } else
          result = await service.attach(
            p,
            draftId,
            parse(attachSchema, await readJSON()),
            condition,
            key,
          );
      } else if (endpoint.path.endsWith("/attachments")) {
        const owner = draftId
          ? service.draft(p, parse(id, draftId)).source_id
          : store.getSource(parse(id, sourceId), p).id;
        const ids = draftId
          ? service.draft(p, draftId).attachments
          : store.db
              .query<{ attachment_id: string }, string>(
                "SELECT attachment_id FROM revision_attachments WHERE revision_id=?",
              )
              .all(store.getSource(owner, p).current_revision)
              .map((r) => r.attachment_id);
        result = { attachments: ids.map((aid) => service.attachment(p, owner, aid)) };
      } else {
        if (endpoint.path.startsWith("/api/v1/drafts/")) {
          const d = service.draft(p, parse(id, draftId));
          const aid = parse(id, c.req.param("attachment_id"));
          if (!d.attachments.includes(aid)) throw new ApiError(404, "attachment_not_found");
          const { attachment, bytes } = await service.attachmentBytes(p, d.source_id, aid);
          const latest = service.draft(service.principal(p, "read"), draftId);
          if (!latest.attachments.includes(aid)) throw new ApiError(404, "attachment_not_found");
          if (bytes.length > 1024 * 1024) throw new ApiError(413, "attachment_too_large_for_json");
          return c.json({
            request_id: c.get("requestId"),
            ...attachment,
            body_base64: bytes.toString("base64"),
          });
        }
        const aid = parse(id, c.req.param("attachment_id"));
        const source = store.getSource(parse(id, sourceId), p);
        if (
          !store.db
            .query("SELECT 1 FROM revision_attachments WHERE revision_id=? AND attachment_id=?")
            .get(source.current_revision, aid)
        )
          throw new ApiError(404, "attachment_not_found");
        const { attachment, bytes } = await service.attachmentBytes(p, sourceId, aid);
        const current = store.getSource(sourceId, service.principal(p, "read"));
        if (current.current_revision !== source.current_revision)
          throw new ApiError(409, "source_changed");
        if (endpoint.path.endsWith("/content")) {
          c.header("Content-Type", attachment.media_type);
          c.header("Content-Disposition", "attachment");
          return c.body(new Uint8Array(bytes));
        }
        if (bytes.length > 1024 * 1024) throw new ApiError(413, "attachment_too_large_for_json");
        result = { ...attachment, body_base64: bytes.toString("base64") };
      }
      if ("etag" in result && typeof result.etag === "string") c.header("ETag", result.etag);
      if ("source_etag" in result && typeof result.source_etag === "string")
        c.header("ETag", result.source_etag);
      const response = { request_id: c.get("requestId"), ...result };
      if (Buffer.byteLength(JSON.stringify(response)) > MAX_JSON)
        throw new ApiError(413, "response_too_large");
      return c.json(response);
    });
  }
  return service;
}
