import { z } from "zod";
import { profileId } from "./ocr";

export const id = z.string().uuid();
const name = z.string().trim().min(1).max(128);
export const grantSchema = z
  .object({
    collection_id: name,
    project_id: name.optional(),
    region: name.optional(),
  })
  .strict();
export type Grant = z.infer<typeof grantSchema>;
export const scopeSchema = z
  .object({
    collection_ids: z.array(name).min(1).max(20),
    project_id: name.optional(),
    region: name.optional(),
  })
  .strict()
  .superRefine((v, ctx) => {
    if (v.collection_ids.includes("assessment") && (!v.project_id || !v.region)) {
      ctx.addIssue({ code: "custom", message: "assessment requires project_id and region" });
    }
  });
export type Scope = z.infer<typeof scopeSchema>;
export const metadataSchema = z
  .object({
    title: z.string().trim().min(1).max(256),
    collection_id: name,
    project_id: name.optional(),
    region: name.optional(),
    source_kind: z.enum(["document", "wiki"]).default("document"),
    language: z.string().min(2).max(16).default("ja"),
    external_key: z.string().min(1).max(256).optional(),
  })
  .strict()
  .superRefine((v, ctx) => {
    if (v.collection_id === "assessment" && (!v.project_id || !v.region)) {
      ctx.addIssue({ code: "custom", message: "assessment requires project_id and region" });
    }
  });
export type Metadata = z.infer<typeof metadataSchema>;
const query = z
  .string()
  .trim()
  .min(1)
  .refine((v) => Buffer.byteLength(v) <= 8192);
export const searchSchema = z
  .object({
    query,
    scope: scopeSchema,
    mode: z.enum(["text", "semantic", "hybrid"]).default("hybrid"),
    limit: z.number().int().min(1).max(50).default(10),
    timeout_ms: z.number().int().min(1).max(10000).default(3000),
    filter: z
      .object({
        source_kinds: z
          .array(z.enum(["document", "wiki"]))
          .min(1)
          .max(2)
          .optional(),
        languages: z.array(z.string().min(2).max(16)).min(1).max(10).optional(),
        include_past_revisions: z.boolean().default(false),
      })
      .strict()
      .default({ include_past_revisions: false }),
  })
  .strict();
export type SearchInput = z.infer<typeof searchSchema>;
export const referenceSchema = z
  .object({
    source_id: id,
    source_revision: id,
    evidence_revision: id,
    context_id: z.string().min(1).max(256),
  })
  .strict();
export type Reference = z.infer<typeof referenceSchema>;
export const contextSchema = z
  .object({
    retrieval_id: id,
    references: z.array(referenceSchema).min(1).max(20),
    max_chars: z.number().int().min(1).max(80000).default(80000),
  })
  .strict();
export const answerSchema = z
  .object({
    query,
    scope: scopeSchema,
    profile: z.literal("default").default("default"),
    timeout_ms: z.number().int().min(1).max(300000).default(180000),
  })
  .strict();
export type AnswerInput = z.infer<typeof answerSchema>;
export const recordSchema = z
  .object({
    id: z.string().min(1).max(256),
    text: z.string().max(2000000),
    parent_id: z.string().nullable().optional(),
    source_sha256: z.string().regex(/^[a-f0-9]{64}$/),
    kind: z.string(),
    unit: z.string().optional(),
    headings: z.array(z.string()).default([]),
    refs: z.array(z.string()).default([]),
    context_refs: z.array(z.string()).default([]),
    pages: z.array(z.number().int().positive()).default([]),
    provenance: z.array(z.record(z.string(), z.unknown())).default([]),
    relations: z.array(z.record(z.string(), z.unknown())).default([]),
    row_range: z.array(z.number()).default([]),
    tables: z.array(z.record(z.string(), z.unknown())).default([]),
    ocr_evidence: z.array(z.record(z.string(), z.unknown())).optional(),
  })
  .strip();
export type RagRecord = z.infer<typeof recordSchema>;
export const extractionSchema = z
  .object({
    source_id: id,
    source_revision: id,
    job_id: id,
    run: z.number().int().positive(),
    source_sha256: z.string().regex(/^[a-f0-9]{64}$/),
    profile: profileId,
    contexts: z.array(recordSchema).max(50000),
    chunks: z.array(recordSchema).max(100000),
    warnings: z.array(z.string()).max(1000).default([]),
    viewer_ready: z.boolean().optional(),
  })
  .strict();
export type Extraction = z.infer<typeof extractionSchema>;
export type Principal = {
  clientId: string;
  subject: string;
  grants: Grant[];
  actions: string[];
  expires: number;
  credential_version: string;
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message = code,
    public retryable = false,
    public details?: unknown,
  ) {
    super(message);
  }
}
export function parse<T>(schema: z.ZodType<T>, value: unknown): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success)
    throw new ApiError(
      422,
      "invalid_input",
      "Invalid request",
      false,
      parsed.error.issues.map((i) => ({ path: i.path, message: i.message })),
    );
  return parsed.data;
}
export const MAX_FILE = 50 * 1024 * 1024;
export const MAX_JSON = 2 * 1024 * 1024;

// FTS selection and ranking must apply the same normalization/deduplication budget.
export function queryTokens(query: string): string[] {
  return [...new Set(query.normalize("NFKC").split(/\s+/u))].filter(Boolean).slice(0, 32);
}

export const viewerRequestSchema = z
  .object({
    source_id: id,
    source_revision: id,
    evidence_revision: id,
    resource: z.string().min(1).max(512).default("manifest"),
    prefix: z.string().regex(/^\/viewer\/session\/[A-Za-z0-9_-]{43}\/$/),
  })
  .strict();
export const viewerManifestSchema = z.object({
  job: z
    .object({
      id: id,
      filename: z.string(),
      state: z.literal("success"),
      pages: z.number().int().nonnegative(),
      preview: z.string(),
      slide_layout: z.boolean(),
    })
    .passthrough(),
  kind: z.enum(["page", "slide", "sheet", "document"]),
  units: z.number().int().positive(),
});
export const viewerResultSchema = z.union([
  viewerManifestSchema,
  z
    .object({
      media_type: z.string().min(1).max(128),
      body_base64: z.string().max(45 * 1024 * 1024),
    })
    .strict(),
]);
