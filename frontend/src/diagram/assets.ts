import { withBase } from "../lib/base";

/**
 * Excalidraw's distributed fonts are served with the app instead of its
 * public CDN. Must run before the library first loads fonts.
 */
export function ensureAssetPath() {
  const target = window as unknown as { EXCALIDRAW_ASSET_PATH?: string };
  target.EXCALIDRAW_ASSET_PATH ??= import.meta.env.DEV
    ? "/node_modules/@excalidraw/excalidraw/dist/dev/"
    : withBase("/static/frontend/excalidraw/");
}

/** Image formats the server stores in a diagram's `files`. */
export const ALLOWED_IMAGE_MIME: readonly string[] = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
];
