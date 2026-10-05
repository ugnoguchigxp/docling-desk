export type View = "preview" | "tables" | "structure" | "rag";
export type Language = "original" | "en" | "ja";
export type Kind = "page" | "slide" | "sheet" | "document";
export interface Job {
  id: string;
  filename: string;
  original_filename?: string | null;
  folder_id: string | null;
  state: "queued" | "running" | "success" | "partial" | "failed";
  created: number;
  duration: number | null;
  pages: number;
  tables: number;
  pictures: number;
  chunks: number;
  search_chunks: number;
  rag_policy: string | null;
  error: string | null;
  preview: string | null;
  slide_layout: boolean;
  synthetic?: boolean;
  translations?: Record<
    string,
    { saved: number; active: number; failed: number }
  >;
}
export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  created: number;
}
export interface Library {
  jobs: Job[];
  folders: Folder[];
}
export type Item = (Job & { kind: "file" }) | (Folder & { kind: "folder" });
export interface ItemRef {
  id: string;
  kind: "folder" | "file";
}
export interface Slide {
  number: number;
  width: number;
  height: number;
  preview: string | null;
}
export interface Slides {
  slides: Slide[];
}
export interface Table {
  ref: string;
  label: string;
  source_label: string;
  pages: number[];
  rows: string[][];
  columns: number;
  header_rows: number;
  merged: boolean;
}
export interface Element {
  label: string;
  ref: string;
  text: string;
  pages: number[];
  parent: string | null;
  captions: string[];
  provenance: unknown[];
}
export interface Chunk {
  id: string;
  kind?: string;
  parent_id?: string;
  unit?: string;
  headings: string[];
  text: string;
  row_range?: number[];
  oversize?: boolean;
  pages: number[];
  refs: string[];
  context_refs?: string[];
  relations?: unknown[];
  source: string;
  source_sha256: string;
}
export interface RagPolicy {
  image_content?: "excluded" | "ocr_text";
  context_chunks: number;
  search_chunks: number;
  docling_chunks: number;
  target_chars: number;
  tolerance_chars?: number;
  split_threshold_chars?: number;
}
export interface TranslationRecord {
  state: string;
  available: boolean;
  stale: boolean;
  error: string | null;
  result_created_at: string | null;
  wait_reason?: string;
  next_attempt_at?: string;
}
export interface TranslationUnit {
  id: string;
  kind: Kind;
  number: number;
  mode: string;
  excluded_count: number;
  segments_count: number;
  languages: Record<"en" | "ja", TranslationRecord>;
}
export interface TranslationOverview {
  units: TranslationUnit[];
  unlocated_count: number;
  profile: { provider?: string };
  configuration_error: string | null;
  scheduling?: { interval_seconds: number };
}
export interface ExplanationUnit {
  id: string;
  kind: Kind;
  number: number;
  name?: string;
  available: boolean;
  stale?: boolean;
  warning?: string;
  storage_error?: string;
  state: {
    state: string;
    stage?: string;
    error?: string;
    review_issues?: string[];
    latest_version_id?: string;
  };
}
export interface ExplanationOverview {
  units: ExplanationUnit[];
  source_error: string | null;
  profile: { web_provider?: string };
  enabled: boolean;
  configuration_error: string | null;
}
export interface Evidence {
  id: string;
  kind: string;
  title: string;
  url?: string;
  pages: number[];
  text: string;
  fetched_at?: string;
  truncated?: boolean;
}
export interface ExplanationValue {
  created_at: string;
  extraction_state: string;
  unlocated_count: number;
  source: {
    kind: Kind;
    excluded_pictures: number;
    blocks: { id: string; pages: number[]; text: string }[];
    tables: Table[];
  };
  local_search: { queries: string[]; evidence: Evidence[] };
  web_search: { queries: string[]; evidence: Evidence[]; status: string };
  explanation: {
    sections: {
      title: string;
      text: string;
      source_ids: string[];
      evidence_ids: string[];
    }[];
    glossary: { term: string; definition: string }[];
    supplements: { title: string; text: string; evidence_ids: string[] }[];
    limitations: string[];
  };
}
export interface ExplanationResult {
  result: ExplanationValue | null;
  source_match: boolean;
}
export const labels = {
  queued: "待機中",
  running: "抽出中",
  success: "抽出完了",
  partial: "一部抽出",
  failed: "失敗",
};
export const unitNames = {
  page: "ページ",
  slide: "スライド",
  sheet: "シート",
  document: "文書全体",
};
export const unitLabel = (kind: Kind, number: number) =>
  kind === "document" ? unitNames.document : `${unitNames[kind]} ${number}`;
export const isDone = (job?: Job | null) =>
  !!job && ["success", "partial"].includes(job.state);
export const itemKey = (item: ItemRef) => `${item.kind}:${item.id}`;
export const itemName = (item: Item) =>
  item.kind === "file" ? item.filename : item.name;
