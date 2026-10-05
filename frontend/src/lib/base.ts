/**
 * The server publishes the reverse-proxy prefix (for example `/assessment`) in
 * a meta tag; routes are always built without it and prefixed at the sink.
 */
function read(): string {
  try {
    const value =
      document
        .querySelector<HTMLMetaElement>('meta[name="docling-base"]')
        ?.content.trim() ?? "";
    return /^\/[\w\-.]+(?:\/[\w\-.]+)*\/?$/.test(value)
      ? value.replace(/\/+$/, "")
      : "";
  } catch {
    return "";
  }
}
export const basePath = read();
/** Prefix a root-relative path; other URLs are returned unchanged. */
export function withBase(path: string): string {
  if (!basePath || !path.startsWith("/") || path.startsWith("//")) return path;
  return path === basePath || path.startsWith(`${basePath}/`)
    ? path
    : basePath + path;
}
