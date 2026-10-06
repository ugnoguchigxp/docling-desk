import { object } from "./value";
import type {
  Chunk,
  Element,
  ExplanationOverview,
  ExplanationResult,
  Slides,
  Table,
  TranslationOverview,
  RagPolicy,
  Library,
} from "./types";
type Check = (value: unknown) => boolean;
const string: Check = (v) => typeof v === "string",
  number: Check = (v) => typeof v === "number" && Number.isFinite(v),
  boolean: Check = (v) => typeof v === "boolean",
  anything: Check = () => true;
const list =
  (check: Check): Check =>
  (v) =>
    Array.isArray(v) && v.every(check);
const maybe =
  (check: Check): Check =>
  (v) =>
    v === undefined || v === null || check(v);
const nullable =
  (check: Check): Check =>
  (v) =>
    v === null || check(v);
const fields =
  (shape: Record<string, Check>): Check =>
  (v) =>
    object(v) && Object.entries(shape).every(([key, check]) => check(v[key]));
const count: Check = (v) =>
  number(v) && Number.isSafeInteger(v) && Number(v) >= 0;
const positive: Check = (v) => number(v) && Number(v) > 0;
const unitNumber: Check = (v) => count(v) && positive(v);
const timestamp: Check = (v) =>
  number(v) && Number.isFinite(new Date(Number(v) * 1000).getTime());
const kind: Check = (v) =>
  v === "page" || v === "slide" || v === "sheet" || v === "document";
function decoder<T>(check: Check) {
  return (value: unknown): T => {
    if (!check(value))
      throw new Error("保存データの応答形式を確認できません。");
    return value as T;
  };
}
export const library = decoder<Library>(
  fields({
    folders: list(
      fields({
        id: string,
        name: string,
        parent_id: nullable(string),
        created: timestamp,
      }),
    ),
    jobs: list(
      fields({
        id: string,
        filename: string,
        original_filename: maybe(string),
        folder_id: nullable(string),
        state: (v) =>
          ["queued", "running", "success", "partial", "failed"].includes(
            String(v),
          ),
        created: timestamp,
        duration: maybe(number),
        pages: count,
        tables: count,
        pictures: count,
        chunks: count,
        search_chunks: count,
        rag_policy: maybe(string),
        error: maybe(string),
        preview: maybe(string),
        slide_layout: boolean,
        synthetic: maybe(boolean),
        translations: maybe(
          (v) =>
            object(v) &&
            Object.values(v).every(
              fields({ saved: count, active: count, failed: count }),
            ),
        ),
      }),
    ),
  }),
);
const record = fields({
  state: string,
  available: boolean,
  stale: boolean,
  error: maybe(string),
  result_created_at: maybe(string),
  next_attempt_at: maybe(string),
  wait_reason: maybe(string),
});
const translationUnit = fields({
  preview_unavailable_reason: maybe(string),
  unavailable_reason: maybe(string),
  id: string,
  kind,
  number: unitNumber,
  mode: string,
  excluded_count: number,
  segments_count: number,
  languages: fields({ en: record, ja: record }),
});
export const translations = decoder<TranslationOverview>(
  fields({
    units: list(translationUnit),
    unlocated_count: number,
    profile: fields({ provider: maybe(string) }),
    configuration_error: maybe(string),
    scheduling: maybe(fields({ interval_seconds: number })),
  }),
);
export const explanations = decoder<ExplanationOverview>(
  fields({
    units: list(
      fields({
        id: string,
        name: maybe(string),
        stale: maybe(boolean),
        warning: maybe(string),
        storage_error: maybe(string),
        kind,
        number: unitNumber,
        available: boolean,
        state: fields({
          state: string,
          stage: maybe(string),
          error: maybe(string),
          review_issues: maybe(list(string)),
          latest_version_id: maybe(string),
        }),
      }),
    ),
    profile: fields({ web_provider: maybe(string) }),
    enabled: boolean,
    source_error: maybe(string),
    configuration_error: maybe(string),
  }),
);
const sourceTableFields = fields({
  ref: string,
  label: string,
  rows: list(list(string)),
  header_rows: count,
  merged: boolean,
});
const sourceTable: Check = (v) =>
  sourceTableFields(v) &&
  object(v) &&
  Array.isArray(v.rows) &&
  Number(v.header_rows) <= v.rows.length;
const evidence = fields({
  id: string,
  kind: string,
  title: maybe(string),
  url: maybe(string),
  pages: maybe(list(number)),
  text: string,
});
const search = fields({ queries: list(string), evidence: list(evidence) });
const explanationValue = fields({
  created_at: string,
  extraction_state: string,
  unlocated_count: number,
  source: fields({
    kind,
    excluded_pictures: number,
    blocks: list(fields({ id: string, pages: list(number), text: string })),
    tables: list(sourceTable),
  }),
  local_search: search,
  web_search: (v) => search(v) && fields({ status: string })(v),
  explanation: fields({
    sections: list(
      fields({
        title: string,
        text: string,
        source_ids: list(string),
        evidence_ids: list(string),
      }),
    ),
    glossary: list(fields({ term: string, definition: string })),
    supplements: list(
      fields({ title: string, text: string, evidence_ids: list(string) }),
    ),
    limitations: list(string),
  }),
});
export const explanationResult = decoder<ExplanationResult>(
  fields({ result: maybe(explanationValue), source_match: boolean }),
);
export const slides = decoder<Slides>(
  fields({
    slides: list(
      fields({
        number: unitNumber,
        width: positive,
        height: positive,
        preview: maybe(string),
      }),
    ),
  }),
);
export const tables = decoder<Table[]>(
  list(
    (v) =>
      sourceTable(v) &&
      fields({ source_label: string, pages: list(unitNumber), columns: count })(
        v,
      ),
  ),
);
export const elements = decoder<Element[]>(
  list(
    fields({
      label: string,
      ref: string,
      text: string,
      pages: list(number),
      parent: maybe(string),
      captions: list(string),
      provenance: list(anything),
    }),
  ),
);
export const chunk = decoder<Chunk>(
  fields({
    id: string,
    text: string,
    headings: list(string),
    pages: list(number),
    refs: list(string),
    source: string,
    source_sha256: string,
    context_refs: maybe(list(string)),
    row_range: maybe(list(number)),
    kind: maybe(string),
    parent_id: maybe(string),
    unit: maybe(string),
    oversize: maybe(boolean),
    relations: maybe(list(anything)),
  }),
);
export const policy = decoder<RagPolicy>(
  fields({
    context_chunks: number,
    search_chunks: number,
    docling_chunks: number,
    target_chars: number,
    tolerance_chars: maybe(number),
    split_threshold_chars: maybe(number),
  }),
);

export const translationPanel = decoder<{
  available: boolean;
  texts: string[];
}>(fields({ available: boolean, texts: list(string) }));
