import { createHash } from "node:crypto";
import { z } from "zod";
import type { Clients } from "./auth";
import { refresh, requireAction } from "./auth";
import { ApiError, id, type Principal } from "./contracts";
import type { Extractor } from "./providers";
import type { Store } from "./store";

const emptyMarkdownHash = createHash("sha256").update("").digest("hex");

const version = { source_id: id, source_revision: id, evidence_revision: id };
const ref = z.string().min(1).max(256);
const offset = z.number().int().min(0).max(100000).default(0);
const limit = z.number().int().min(1).max(200).default(100);
const selector = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("section"), ref }).strict(),
  z.object({ kind: z.literal("elements"), start_ref: ref, end_ref: ref }).strict(),
  z
    .object({
      kind: z.literal("units"),
      start: z.number().int().positive(),
      end: z.number().int().positive(),
    })
    .strict(),
]);
const kind = z.enum(["page", "slide", "sheet", "document"]);
const next = z.number().int().nonnegative().nullable();
const provenance = z.array(z.record(z.string(), z.unknown()));
const entry = z
  .object({
    ref,
    label: z.string(),
    level: z.number().int().nonnegative(),
    parent: z.string().nullable(),
    pages: z.array(z.number().int().positive()),
    heading: z.string().nullable(),
  })
  .strict();
const tableInfo = z
  .object({
    table_ref: ref,
    caption: z.string(),
    pages: z.array(z.number().int().positive()),
    rows: z.number().int().nonnegative(),
    columns: z.number().int().nonnegative(),
    header_rows: z.number().int().nonnegative(),
    merged: z.boolean(),
    provenance,
  })
  .strict();
export const readResults = {
  outline: z
    .object({
      kind,
      units: z.array(
        z.object({ number: z.number().int().positive(), name: z.string().nullable() }),
      ),
      entries: z.array(entry).max(200),
      total: z.number().int().nonnegative(),
      next_offset: next,
    })
    .strict(),
  section: z
    .object({
      kind,
      selector,
      elements: z.array(entry.extend({ text: z.string(), provenance })).max(1000),
      chars: z.number().int().nonnegative(),
    })
    .strict(),
  tables: z
    .object({
      tables: z.array(tableInfo).max(200),
      total: z.number().int().nonnegative(),
      next_offset: next,
    })
    .strict(),
  table: tableInfo
    .extend({
      row_offset: z.number().int().nonnegative(),
      column_offset: z.number().int().nonnegative(),
      next_row_offset: next,
      next_column_offset: next,
      format: z.enum(["json", "csv"]),
      cells: z.array(z.record(z.string(), z.unknown())),
      data: z.array(z.array(z.string())),
      csv: z.string().optional(),
    })
    .strict(),
  "page-image": z
    .object({
      kind: z.enum(["page", "slide"]),
      unit: z.number().int().positive(),
      media_type: z.literal("image/webp"),
      width: z.number().int().positive().max(1536),
      height: z.number().int().positive().max(1536),
      body_base64: z
        .string()
        .max(1398104)
        .regex(/^[A-Za-z0-9+/]+={0,2}$/),
    })
    .strict(),
};
export const READ_ENDPOINTS = [
  { operation: "outline", input: z.object({ ...version, offset, limit }).strict() },
  {
    operation: "section",
    input: z
      .object({
        ...version,
        selector,
        max_chars: z.number().int().min(1).max(80000).default(40000),
      })
      .strict(),
  },
  { operation: "tables", input: z.object({ ...version, offset, limit }).strict() },
  {
    operation: "table",
    input: z
      .object({
        ...version,
        table_ref: ref,
        row_offset: z.number().int().min(0).max(1000000).default(0),
        row_limit: z.number().int().min(1).max(100).default(100),
        column_offset: z.number().int().min(0).max(1000000).default(0),
        column_limit: z.number().int().min(1).max(50).default(50),
        format: z.enum(["json", "csv"]).default("json"),
      })
      .strict(),
  },
  {
    operation: "page-image",
    input: z.object({ ...version, unit: z.number().int().min(1).max(100000) }).strict(),
  },
] as const;
export const readPath = (operation: string) => `/api/v1/document-reads/${operation}`;
export const readResponseSchemas = Object.fromEntries(
  READ_ENDPOINTS.map((e) => [
    `post ${readPath(e.operation)}`,
    readResults[e.operation].extend(version),
  ]),
);
export type ArtifactRead = {
  source_id: string;
  source_revision: string;
  job_id: string;
  run: number;
  suffix: string;
  options: Record<string, unknown> & { operation: keyof typeof readResults };
};
export async function documentRead(
  store: Store,
  clients: Clients,
  extractor: Extractor,
  principal: Principal,
  operation: keyof typeof readResults,
  input: Record<string, unknown>,
) {
  const reference = input as {
    source_id: string;
    source_revision: string;
    evidence_revision: string;
  };
  const check = () => {
    const p = refresh(principal, clients());
    requireAction(p, "read");
    const source = store.getSource(reference.source_id, p);
    if (source.current_revision !== reference.source_revision)
      throw new ApiError(409, "source_changed");
    const revision = store.revision(reference.source_revision);
    if (!revision?.evidence_revision || revision.evidence_revision !== reference.evidence_revision)
      throw new ApiError(409, "evidence_changed");
    const artifact = store.viewerArtifact(revision.id);
    // The existing worker publishes empty Markdown without an extraction attempt.
    // Its immutable source hash proves that no elements/tables can exist.
    const empty =
      [".md", ".markdown"].includes(revision.suffix) && revision.sha256 === emptyMarkdownHash;
    if (
      !revision.fts_ready ||
      (!empty && (!artifact || artifact.evidence_revision !== reference.evidence_revision))
    )
      throw new ApiError(409, "document_read_not_ready");
    return { revision, artifact, empty };
  };
  const { revision, artifact, empty } = check();
  const { source_id, source_revision, evidence_revision, ...options } = input;
  let raw: unknown;
  if (empty) {
    if (operation === "outline")
      raw = { kind: "document", units: [], entries: [], total: 0, next_offset: null };
    else if (operation === "tables") raw = { tables: [], total: 0, next_offset: null };
    else
      throw new ApiError(
        operation === "table" ? 404 : 422,
        operation === "table"
          ? "document_table_not_found"
          : operation === "page-image"
            ? "page_image_unsupported"
            : "invalid_document_range",
      );
  } else {
    if (!artifact || !extractor.documentRead) throw new ApiError(503, "document_read_unavailable");
    raw = await extractor.documentRead({
      source_id: reference.source_id,
      source_revision: reference.source_revision,
      suffix: revision.suffix,
      job_id: artifact.job_id,
      run: artifact.run,
      options: { ...options, operation },
    });
  }
  const result = readResults[operation].safeParse(raw);
  check(); // Revalidate authorization, source and evidence after processor I/O.
  if (!result.success) throw new ApiError(502, "invalid_document_read_response");
  const output = { ...result.data, source_id, source_revision, evidence_revision };
  if (Buffer.byteLength(JSON.stringify(output)) > 1900000)
    throw new ApiError(413, "document_read_too_large");
  return output;
}
