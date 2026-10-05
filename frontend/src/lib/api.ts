import type { Library } from "./types";
import { library as decodeLibrary } from "./contracts";
import { object } from "./value";
import { withBase } from "./base";
export { object } from "./value";
export async function request<T>(
  url: string,
  options: RequestInit = {},
  decode?: (value: unknown) => T,
): Promise<T> {
  const response = await fetch(withBase(url), { cache: "no-store", ...options });
  const value: unknown = await response.json().catch(() => {
    throw new Error(
      response.ok ? "応答を読み込めませんでした。" : `HTTP ${response.status}`,
    );
  });
  if (
    response.status === 401 &&
    object(value) &&
    typeof value.login_url === "string"
  ) {
    // The shared login expired: return through the host application's login.
    window.location.assign(
      `${value.login_url}${value.login_url.includes("?") ? "&" : "?"}next=${encodeURIComponent(window.location.href)}`,
    );
  }
  if (!response.ok)
    throw new Error(
      object(value) && typeof value.detail === "string"
        ? value.detail
        : `HTTP ${response.status}`,
    );
  return decode ? decode(value) : (value as T);
}
export async function library(signal: AbortSignal): Promise<Library> {
  return request("/api/library", { signal }, decodeLibrary);
}
export const post = <T>(url: string, body: unknown) =>
  request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const fileUrl = (id: string, name: string, download = false) =>
  `/files/${encodeURIComponent(id)}/${name.split("/").map(encodeURIComponent).join("/")}${download ? "?download=true" : ""}`;
export function originalUrl(job: {
  id: string;
  filename: string;
  original_filename?: string | null;
}) {
  const name = job.original_filename || job.filename;
  return fileUrl(
    job.id,
    `original${name.slice(name.lastIndexOf(".")).toLowerCase()}`,
    true,
  );
}
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
