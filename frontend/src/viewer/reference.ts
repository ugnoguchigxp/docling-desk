import type { Job } from "../lib/types";
export interface DocumentReference {
  version?: 1;
  source_id: string;
  source_revision: string;
  evidence_revision: string;
  location?: {
    kind: "page" | "slide" | "sheet" | "document";
    number: number;
  } | null;
}
export interface ViewerManifest {
  reference: DocumentReference;
  job: Job;
  kind: "page" | "slide" | "sheet" | "document";
  units: number;
}
