import { z } from "zod";

export const ocrProvider = z.enum(["local", "azure_read", "disabled"]);
export const profileId = z.enum([
  "local-v1",
  "azure-read-v1",
  "disabled-v1",
  "azure-read-full-page-v1",
]);
export const ocrProfile = z
  .object({
    provider: ocrProvider,
    enabled: z.boolean(),
    endpoint: z.string().max(2048),
    auth: z.enum(["api_key", "managed_identity"]),
    client_id: z.string().nullable(),
    api_version: z.literal("2024-11-30"),
    model: z.literal("prebuilt-read"),
    profile: z.enum(["read-v1", "read-full-page-v1"]),
    tier: z.enum(["F0", "S0"]),
    document_timeout: z.number().int().min(180).max(7200),
    max_submissions: z.number().int().min(1).max(1000),
    adapter_version: z.literal("1"),
  })
  .strict();
export const profileSelection = z.object({ profile: profileId, ocr_profile: ocrProfile }).strict();
export type ProfileSelection = z.infer<typeof profileSelection>;
export const ocrRecord = z
  .object({
    key: z.string().regex(/^[a-f0-9]{64}$/),
    state: z.enum([
      "prepared",
      "submitting",
      "submitted",
      "polling",
      "completed",
      "failed",
      "cancelled",
      "submission_unknown",
    ]),
    attempt: z.number().int().min(0).max(1000),
    version: z.number().int().nonnegative().optional(),
    metadata: z.record(z.string(), z.unknown()),
    operation: z.string().max(4096).nullable(),
    response_sha256: z
      .string()
      .regex(/^[a-f0-9]{64}$/)
      .nullable(),
    error_code: z.string().max(100).nullable(),
    request_id: z.string().max(256).nullable().optional(),
  })
  .strict();
export type OcrRecord = z.infer<typeof ocrRecord>;
export const ocrRequest = z
  .object({
    key: z.string().regex(/^[a-f0-9]{64}$/),
    run: z.number().int().positive(),
    source_revision: z.string().uuid(),
    action: z.enum(["get", "save"]),
    expected_version: z.number().int().nonnegative().optional(),
    record: ocrRecord.optional(),
  })
  .strict();
