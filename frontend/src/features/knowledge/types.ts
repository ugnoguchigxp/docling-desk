import type { ArticleInfo } from "../wiki-draft";
import type { EditorDocument } from "../../editor/WikiPlateEditor";

export interface ArticleSummary {
  id: string;
  title: string;
  namespace: string;
  path: string;
  revision: string;
  language?: string;
  translation_group?: string;
  source_job_id?: string;
  source_unit?: number;
  translation_status?:
    "untranslated" | "translated" | "reviewed" | "needs_review";
  entry_kind?: "csv";
}
export interface Article extends ArticleSummary {
  updated?: number;
  article_info?: ArticleInfo;
  editor_document?: EditorDocument | null;
  body: string;
  html: string;
  outline: { title: string; anchor: string; level: number }[];
  related_document: { job_id: string; title: string } | null;
  fallback_original?: string | null;
}
export interface Hit {
  chunk_id: string;
  source_id: string;
  kind: "wiki" | "document";
  title: string;
  text: string;
  job_id: string | null;
  unit: number;
  partial: boolean;
  locator: { anchor?: string; heading?: string; refs?: string[] };
}
export interface Task {
  id: string;
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  error?: string;
  progress: number;
  total: number;
  output: {
    results?: Hit[];
    notice?: string;
    stale?: boolean;
    answer?: string;
    citations?: (Hit & { label: string })[];
    unknowns?: string[];
  };
}
export interface IndexStatus {
  azure_configured: boolean;
  configuration_error: string;
  chunks: number;
  embedded: number;
  cooldown_until: number | null;
  jobs: Task[];
}
