import { z } from "zod";
import { grantSchema, id, MAX_JSON, metadataSchema } from "../contracts";

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
export type DraftRow = {
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
export type AttachmentRow = {
  id: string;
  source_id: string;
  draft_id: string;
  filename: string;
  media_type: string;
  size: number;
  sha256: string;
};
