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
  const response = await fetch(withBase(url), {
    cache: "no-store",
    ...options,
  });
  const value: unknown = await response.json().catch(() => {
    throw new Error(
      response.ok ? "応答を読み込めませんでした。" : `HTTP ${response.status}`,
    );
  });
  if (response.status === 401) {
    const target = loginRedirect(value, window.location);
    // The shared login expired: return through the host application's login.
    if (target) window.location.assign(target);
  }
  if (!response.ok)
    throw new Error(
      object(value) && typeof value.detail === "string"
        ? value.detail
        : `HTTP ${response.status}`,
    );
  return decode ? decode(value) : (value as T);
}
/**
 * Login URL for a 401 response, using the return parameter and format that the
 * server is configured with (absolute URL, or the site-internal path).
 */
export function loginRedirect(
  body: unknown,
  here: Pick<Location, "href" | "pathname" | "search" | "hash">,
): string | null {
  if (!object(body) || typeof body.login_url !== "string") return null;
  const param =
    typeof body.return_param === "string" ? body.return_param : "next";
  const back =
    body.return_format === "path"
      ? here.pathname + here.search + here.hash
      : here.href;
  return `${body.login_url}${body.login_url.includes("?") ? "&" : "?"}${encodeURIComponent(param)}=${encodeURIComponent(back)}`;
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
