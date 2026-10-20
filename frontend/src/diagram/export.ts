import type { BinaryFiles } from "@excalidraw/excalidraw/types";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import { object } from "../lib/value";
import { ALLOWED_IMAGE_MIME, ensureAssetPath } from "./assets";

export const EXPORT_SCALE = 2;
export const EXPORT_QUALITY = 0.85;
export const MAX_EDGE = 4096;
export const MAX_PIXELS = 16_000_000;
const PADDING = 10;

export type RasterFormat = "webp" | "jpeg";
export interface RasterResult {
  blob: Blob;
  format: RasterFormat;
}

const MIME: Record<RasterFormat, string> = {
  webp: "image/webp",
  jpeg: "image/jpeg",
};
// Keep in sync with ELEMENT_TYPES in
// src/docling_desk/documents/diagram_models.py (what the server accepts).
const ALLOWED_TYPES = new Set([
  "rectangle",
  "diamond",
  "ellipse",
  "arrow",
  "line",
  "freedraw",
  "text",
  "image",
  "frame",
]);
const ALLOWED_IMAGES = new Set(ALLOWED_IMAGE_MIME);

/**
 * Output size for a scene of `width` x `height` (including padding): the
 * requested scale, reduced so neither edge exceeds `maxEdge` and the total
 * stays within `maxPixels`. Dimensions are floored, never rounded up, so the
 * limits always hold.
 */
export function fitExport(
  width: number,
  height: number,
  options: { scale?: number; maxEdge?: number; maxPixels?: number } = {},
): { width: number; height: number; scale: number } {
  const {
    scale: wanted = EXPORT_SCALE,
    maxEdge = MAX_EDGE,
    maxPixels = MAX_PIXELS,
  } = options;
  const w = Math.max(1, width),
    h = Math.max(1, height);
  const scale = Math.min(
    wanted,
    maxEdge / Math.max(w, h),
    Math.sqrt(maxPixels / (w * h)),
  );
  return {
    width: Math.max(1, Math.floor(w * scale)),
    height: Math.max(1, Math.floor(h * scale)),
    scale,
  };
}

/** Links that may be written to an export; mirrors the server's scheme list. */
export function safeLink(link: unknown): string | null {
  if (typeof link !== "string" || !link) return null;
  try {
    const url = new URL(link);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? link : null;
  } catch {
    return null;
  }
}

interface Prepared {
  elements: readonly ExcalidrawElement[];
  appState: Record<string, unknown>;
  files: BinaryFiles;
}

/**
 * Parse and restore a scene for export. Only the element types and image
 * formats the server accepts are drawn, and unsafe links are dropped, so an
 * export never carries something the saved diagram could not.
 */
export async function prepareScene(scene: string): Promise<Prepared> {
  ensureAssetPath();
  const parsed: unknown = JSON.parse(scene);
  if (!object(parsed)) throw new Error("図表を読み込めませんでした。");
  const { restore } = await import("@excalidraw/excalidraw");
  const restored = restore(
    {
      elements: Array.isArray(parsed.elements) ? parsed.elements : [],
      appState: object(parsed.appState) ? parsed.appState : undefined,
      files: object(parsed.files) ? (parsed.files as BinaryFiles) : undefined,
    },
    null,
    null,
  );
  const files: BinaryFiles = {};
  for (const [id, file] of Object.entries(restored.files ?? {}))
    if (ALLOWED_IMAGES.has(file.mimeType)) files[id] = file;
  const elements = restored.elements
    .filter((e) => !e.isDeleted && ALLOWED_TYPES.has(e.type))
    .filter((e) => e.type !== "image" || (e.fileId && files[e.fileId]))
    .map((e) => (e.link && !safeLink(e.link) ? { ...e, link: null } : e));
  if (!elements.length) throw new Error("書き出す図形がありません。");
  return {
    elements,
    appState: {
      ...restored.appState,
      exportBackground: true,
      exportWithDarkMode: false,
      exportEmbedScene: false,
    },
    files,
  };
}

async function svgText(scene: string): Promise<string> {
  const { exportToSvg } = await import("@excalidraw/excalidraw");
  const { elements, appState, files } = await prepareScene(scene);
  const svg = await exportToSvg({
    elements: elements as never,
    appState: appState as never,
    files,
    exportPadding: PADDING,
  });
  return new XMLSerializer().serializeToString(svg);
}

export async function exportSvg(scene: string): Promise<Blob> {
  return new Blob(
    [`<?xml version="1.0" encoding="UTF-8"?>\n${await svgText(scene)}`],
    {
      type: "image/svg+xml",
    },
  );
}

function toBlob(
  canvas: HTMLCanvasElement,
  format: RasterFormat,
): Promise<Blob | null> {
  return new Promise((resolve) =>
    canvas.toBlob(resolve, MIME[format], EXPORT_QUALITY),
  );
}

function onWhite(source: HTMLCanvasElement): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = source.width;
  canvas.height = source.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("画像を作成できませんでした。");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(source, 0, 0);
  return canvas;
}

/**
 * Lossy raster export. WebP is encoded when the browser supports it;
 * otherwise (the encoder silently produces PNG) JPEG is returned and
 * `format` says so.
 */
export async function exportRaster(
  scene: string,
  format: RasterFormat,
): Promise<RasterResult> {
  const { exportToCanvas } = await import("@excalidraw/excalidraw");
  const { elements, appState, files } = await prepareScene(scene);
  const canvas = await exportToCanvas({
    elements: elements as never,
    appState: appState as never,
    files,
    exportPadding: PADDING,
    getDimensions: (width: number, height: number) => fitExport(width, height),
  });
  if (format === "webp") {
    const blob = await toBlob(canvas, "webp");
    if (blob && blob.type === MIME.webp) return { blob, format: "webp" };
  }
  const blob = await toBlob(onWhite(canvas), "jpeg");
  if (!blob) throw new Error("画像を作成できませんでした。");
  return { blob, format: "jpeg" };
}

const escapeHtml = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ] as string,
  );

function base64(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export const HTML_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'";

/**
 * Static HTML page with no script. The SVG is embedded as a data: image so the
 * page's `default-src 'none'` policy still allows its inlined fonts, and so
 * nothing in the drawing can execute.
 */
export async function exportHtml(scene: string, title = "図表"): Promise<Blob> {
  const svg = await svgText(scene);
  const html = `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${HTML_CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>body{margin:0;padding:16px;background:#fff}img{display:block;max-width:100%;height:auto;margin:0 auto}</style>
</head>
<body>
<img alt="${escapeHtml(title)}" src="data:image/svg+xml;base64,${base64(svg)}">
</body>
</html>
`;
  return new Blob([html], { type: "text/html" });
}
