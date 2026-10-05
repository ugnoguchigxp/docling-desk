import { z } from "zod";
import { id, recordSchema, referenceSchema, viewerResultSchema } from "./contracts";
import { generatedSchema } from "./providers";

const envelope = { request_id: id };
const locator = z.record(z.string(), z.unknown());
const hit = referenceSchema.extend({
  chunk_id: id,
  title: z.string(),
  source_kind: z.enum(["document", "wiki"]),
  language: z.string(),
  excerpt: z.string(),
  locator,
  rank: z.number().int().positive(),
  match_reasons: z.array(z.enum(["text", "semantic"])),
  is_current: z.boolean(),
  source_url: z.string().startsWith("/api/v1/sources/"),
});
export const searchResponse = z
  .object({
    ...envelope,
    retrieval_id: id,
    status: z.enum(["complete", "partial"]),
    requested_mode: z.enum(["text", "semantic", "hybrid"]),
    effective_mode: z.enum(["text", "semantic", "hybrid"]),
    degraded_reasons: z.array(z.string()),
    index_state: z.object({
      pending_sources: z.number().int().nonnegative(),
      failed_sources: z.number().int().nonnegative(),
      semantic_pending_sources: z.number().int().nonnegative(),
      snapshot_id: z.string(),
      indexed_at: z.string().nullable(),
    }),
    results: z.array(hit).max(50),
  })
  .strict();
export const contextResponse = z
  .object({
    ...envelope,
    retrieval_id: id,
    contexts: z
      .array(
        recordSchema.extend({
          ...referenceSchema.shape,
          evidence_id: id,
          is_current: z.boolean(),
          locator,
        }),
      )
      .max(20),
  })
  .strict();
export const sourceResponse = z
  .object({
    ...envelope,
    source_id: id,
    title: z.string(),
    source_kind: z.enum(["document", "wiki"]),
    language: z.string(),
    scope: z.object({
      collection_id: z.string(),
      project_id: z.string().nullable(),
      region: z.string().nullable(),
    }),
    source_revision: id,
    evidence_revision: id.nullable(),
    state: z.string(),
    fts_ready: z.boolean(),
    semantic_ready: z.boolean(),
    filename: z.string().optional(),
    warnings: z.array(z.string()),
    created_at: z.string(),
    source_url: z.string(),
  })
  .strict();
export const registrationResponse = z
  .object({ ...envelope, source_id: id, source_revision: id, job_id: id })
  .strict();
export const jobAcceptedResponse = z
  .object({ ...envelope, source_id: id.optional(), job_id: id })
  .strict();
export const jobResponse = z
  .object({
    ...envelope,
    job_id: id,
    source_id: id.nullable(),
    source_revision: id.nullable(),
    kind: z.enum(["extract", "delete", "answer"]),
    state: z.enum(["queued", "running", "completed", "failed", "superseded", "cancelled"]),
    stage: z.string(),
    error_code: z.string().nullable(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();
export const answerAcceptedResponse = z.object({ ...envelope, answer_id: id, job_id: id }).strict();
export const answerResponse = z
  .object({
    ...envelope,
    answer_id: id,
    state: z.enum([
      "queued",
      "running",
      "completed",
      "failed",
      "insufficient_evidence",
      "cancelled",
    ]),
    error_code: z.string().nullable(),
    result: generatedSchema
      .extend({
        retrieval_id: id,
        degraded_reasons: z.array(z.string()),
        created_at: z.string(),
        citations: z.array(
          referenceSchema.extend({ evidence_id: id, locator, source_url: z.string() }),
        ),
      })
      .nullable(),
  })
  .strict();
export const errorResponse = z
  .object({
    ...envelope,
    error: z
      .object({
        code: z.string(),
        message: z.string(),
        retryable: z.boolean(),
        details: z.unknown().optional(),
      })
      .strict(),
  })
  .strict();
export const responseSchemas: Record<string, z.ZodType> = {
  "get /api/v1/openapi.json": z.record(z.string(), z.unknown()),
  "post /api/v1/viewer": viewerResultSchema,
  "post /api/v1/search": searchResponse,
  "post /api/v1/context": contextResponse,
  "get /api/v1/sources/{source_id}": sourceResponse,
  "post /api/v1/sources": registrationResponse,
  "post /api/v1/sources/{source_id}/revisions": registrationResponse,
  "delete /api/v1/sources/{source_id}": jobAcceptedResponse,
  "post /api/v1/sources/{source_id}/reindex": jobAcceptedResponse,
  "post /api/v1/sources/{source_id}/reextract": jobAcceptedResponse,
  "post /api/v1/jobs/{job_id}/retry": jobAcceptedResponse,
  "get /api/v1/jobs/{job_id}": jobResponse,
  "post /api/v1/answers": answerAcceptedResponse,
  "get /api/v1/answers/{answer_id}": answerResponse,
  "get /health/live": z.object({ ...envelope, status: z.literal("alive") }).strict(),
  "get /health/ready": z
    .object({
      ...envelope,
      status: z.literal("ready"),
      capabilities: z.object({
        text: z.boolean(),
        semantic: z.boolean(),
        answers: z.boolean(),
        source_management: z.boolean(),
        history: z.boolean(),
      }),
    })
    .strict(),
};
