import type { Library } from "./types";
import { library as decodeLibrary } from "./contracts";
import { originalName } from "./formats";
import { requestJson } from "./http";
export { object } from "./value";
export { leaveForLogin, loginRedirect } from "./http";
/** JSON request. Failures are `HttpError` (see ./http). */
export const request = requestJson;
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
  const name = originalName(job);
  return fileUrl(
    job.id,
    `original${name.slice(name.lastIndexOf(".")).toLowerCase()}`,
    true,
  );
}
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
