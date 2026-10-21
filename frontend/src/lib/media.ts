import type { ImageInfo, ImageMetadata, Job } from "./types";
import { isPdf, isPptx, originalName } from "./formats";
import { withBase } from "./base";

export interface MediaAsset extends ImageInfo, ImageMetadata {
  asset_id: string;
  display_name: string;
  original_filename: string;
  folder_path: string[];
  metadata_etag: string;
}

export function assetId(jobId: string) {
  return [
    jobId.slice(0, 8),
    jobId.slice(8, 12),
    jobId.slice(12, 16),
    jobId.slice(16, 20),
    jobId.slice(20),
  ].join("-");
}

export function imageMarkdown(alt: string, reference: string) {
  const label = alt
    .replaceAll("\\", "\\\\")
    .replaceAll("[", "\\[")
    .replaceAll("]", "\\]")
    .replace(/[\r\n]+/g, " ");
  return `![${label}](${reference})`;
}

export function thumbnailUrl(job: Job) {
  if (job.image) return withBase(`/api/v1/media/${assetId(job.id)}/thumbnail`);
  if (!job.preview || !["success", "partial"].includes(job.state)) return null;
  const name = originalName(job);
  if (isPdf(name)) return withBase(`/api/jobs/${job.id}/pdf/pages/1/thumbnail`);
  if (isPptx(name)) return withBase(`/api/jobs/${job.id}/slides/1/thumbnail`);
  return null;
}
