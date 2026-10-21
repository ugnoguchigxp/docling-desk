import { object } from "./value";
import { withBase } from "./base";

/**
 * Shared transport. Every HTTP call goes through `send`, which owns the root
 * path, the host login redirect and the error contract; the `read*` functions
 * and `request*` wrappers pick the body type. Nothing here retries: a request
 * whose outcome is unknown (status 0 after a non-GET) is the caller's to
 * resolve, usually with its idempotency key.
 */

/**
 * Failure of one HTTP exchange. `status` is 0 when no response arrived
 * (network failure); a malformed 2xx body keeps the response status.
 * `body` is the parsed JSON, or a clipped copy of a non-JSON error text.
 */
export class HttpError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: unknown;
  /** Response headers (ETag, Retry-After, ...); null when none arrived. */
  readonly headers: Headers | null;
  constructor(
    status: number,
    detail: string,
    body: unknown = null,
    headers: Headers | null = null,
  ) {
    super(detail);
    this.name = "HttpError";
    this.status = status;
    this.detail = detail;
    this.body = body;
    this.headers = headers;
  }
}

/** True for `HttpError`, and for one with exactly `status` when given. */
export const isHttpError = (
  error: unknown,
  status?: number,
): error is HttpError =>
  error instanceof HttpError &&
  (status === undefined || error.status === status);

/** Cancellation by the caller's AbortSignal, never a communication failure. */
export const isAbort = (error: unknown) =>
  typeof error === "object" &&
  error !== null &&
  "name" in error &&
  error.name === "AbortError";

const NETWORK = "サーバーに接続できませんでした。通信を確認してください。";
const MALFORMED = "応答を読み込めませんでした。";
const BODY_LIMIT = 2000;

/** Message for a failed response: FastAPI `detail` (text or validation list). */
export function detailOf(status: number, body: unknown): string {
  if (object(body)) {
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail))
      return body.detail
        .map((issue) =>
          object(issue) && typeof issue.msg === "string"
            ? issue.msg.replace(/^Value error, /, "")
            : "入力内容を確認してください。",
        )
        .join("\n");
  }
  return `HTTP ${status}`;
}

let leaving = false;
/** The shared login expired: return through the host application's login.
 * Concurrent requests fail together; only the first one navigates. */
export function leaveForLogin(body: unknown) {
  const target = loginRedirect(body, window.location);
  if (target && !leaving) {
    leaving = true;
    window.location.assign(target);
  }
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

async function readFailure(response: Response): Promise<unknown> {
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    if (isAbort(error)) throw error;
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text ? text.slice(0, BODY_LIMIT) : null;
  }
}

/** Resolves with a 2xx response; any other outcome throws (see `HttpError`). */
export async function send(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(withBase(url), { cache: "no-store", ...init });
  } catch (error) {
    if (isAbort(error) || init.signal?.aborted) throw error;
    throw new HttpError(0, NETWORK);
  }
  if (response.ok) return response;
  const body = await readFailure(response);
  if (response.status === 401) leaveForLogin(body);
  throw new HttpError(
    response.status,
    detailOf(response.status, body),
    body,
    response.headers,
  );
}

async function bodyText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new HttpError(0, NETWORK, null, response.headers);
  }
}

/** A JSON body. Empty (204) and unparsable bodies are malformed responses. */
export async function readJson(response: Response): Promise<unknown> {
  const text = await bodyText(response);
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(response.status, MALFORMED, null, response.headers);
  }
}

/** The body as it is: original files, scenes, JSONL. */
export const readText = bodyText;

/** For responses whose body does not matter (DELETE 204, acknowledgements). */
export async function discard(response: Response): Promise<void> {
  try {
    await response.body?.cancel();
  } catch {
    // The status already tells whether the server accepted the call.
  }
}

export const etagOf = (response: Response) => response.headers.get("ETag");

export async function requestJson<T>(
  url: string,
  init: RequestInit = {},
  decode?: (value: unknown) => T,
): Promise<T> {
  const value = await readJson(await send(url, init));
  return decode ? decode(value) : (value as T);
}

export interface TextResponse {
  text: string;
  etag: string | null;
  contentType: string | null;
}
export async function requestText(
  url: string,
  init: RequestInit = {},
): Promise<TextResponse> {
  const response = await send(url, init);
  return {
    text: await readText(response),
    etag: etagOf(response),
    contentType: response.headers.get("Content-Type"),
  };
}

/** Succeeds on any 2xx (204 included) and returns the ETag, if any. */
export async function requestEmpty(
  url: string,
  init: RequestInit = {},
): Promise<{ etag: string | null }> {
  const response = await send(url, init);
  await discard(response);
  return { etag: etagOf(response) };
}

/** The same failure (status, body, headers) with the caller's own message. */
export const withDetail = (error: HttpError, detail: string) =>
  new HttpError(error.status, detail, error.body, error.headers);
