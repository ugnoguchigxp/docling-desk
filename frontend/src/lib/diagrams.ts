import { HttpError, discard, etagOf, readJson, readText, send } from "./http";
import { object } from "./value";

export const SCENE_TYPE = "application/vnd.excalidraw+json";

export interface DraftInfo {
  id: string;
  version: number;
  base_revision: string;
  etag: string;
  updated: number;
}
export interface DiagramInfo {
  id: string;
  name: string;
  folder_id: string | null;
  revision_id: string;
  revision_number: number;
  sha256: string;
  size: number;
  etag: string;
  created: number;
  updated: number;
  draft: DraftInfo | null;
  operation_id: string | null;
}
export interface RevisionInfo {
  id: string;
  number: number;
  parent: string | null;
  sha256: string;
  size: number;
  created: number;
  restored_from: string | null;
  current: boolean;
}
export interface RevisionPage {
  items: RevisionInfo[];
  next: number | null;
}
export interface SceneResponse {
  scene: string;
  etag: string;
}
export interface InfoResponse {
  info: DiagramInfo;
  etag: string;
}
export interface DraftResponse {
  draft: DraftInfo;
  etag: string;
}

/** Migration adapter: diagram call sites now use `HttpError` directly. */
export const DiagramRequestError = HttpError;

const hex32 = /[0-9a-f]{32}/;
/** The existing draft id carried by a 409 from draft creation, when present. */
export function existingDraftId(error: unknown): string | null {
  if (!(error instanceof HttpError) || error.status !== 409) return null;
  const detail = object(error.body) ? error.body.detail : null;
  if (object(detail) && typeof detail.draft_id === "string")
    return detail.draft_id;
  return hex32.exec(error.detail)?.[0] ?? null;
}

const text = (v: unknown): v is string => typeof v === "string";
const num = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);

function decodeDraft(value: unknown): DraftInfo | null {
  if (
    !object(value) ||
    !text(value.id) ||
    !num(value.version) ||
    !text(value.base_revision) ||
    !text(value.etag) ||
    !num(value.updated)
  )
    return null;
  return {
    id: value.id,
    version: value.version,
    base_revision: value.base_revision,
    etag: value.etag,
    updated: value.updated,
  };
}
function decodeInfo(value: unknown): DiagramInfo | null {
  if (
    !object(value) ||
    !text(value.id) ||
    !text(value.name) ||
    !(value.folder_id === null || text(value.folder_id)) ||
    !text(value.revision_id) ||
    !num(value.revision_number) ||
    !text(value.sha256) ||
    !num(value.size) ||
    !text(value.etag) ||
    !num(value.created) ||
    !num(value.updated)
  )
    return null;
  const draft = value.draft == null ? null : decodeDraft(value.draft);
  if (value.draft != null && !draft) return null;
  return {
    id: value.id,
    name: value.name,
    folder_id: value.folder_id,
    revision_id: value.revision_id,
    revision_number: value.revision_number,
    sha256: value.sha256,
    size: value.size,
    etag: value.etag,
    created: value.created,
    updated: value.updated,
    draft,
    operation_id: text(value.operation_id) ? value.operation_id : null,
  };
}
function decodeRevision(value: unknown): RevisionInfo | null {
  if (
    !object(value) ||
    !text(value.id) ||
    !num(value.number) ||
    !(value.parent === null || text(value.parent)) ||
    !text(value.sha256) ||
    !num(value.size) ||
    !num(value.created) ||
    !(value.restored_from === null || text(value.restored_from)) ||
    typeof value.current !== "boolean"
  )
    return null;
  return {
    id: value.id,
    number: value.number,
    parent: value.parent,
    sha256: value.sha256,
    size: value.size,
    created: value.created,
    restored_from: value.restored_from,
    current: value.current,
  };
}

const malformed = (response: Response) =>
  new HttpError(
    response.status,
    "応答の形式が正しくありません。",
    null,
    response.headers,
  );

async function infoOf(response: Response): Promise<InfoResponse> {
  const info = decodeInfo(await readJson(response));
  if (!info) throw malformed(response);
  return { info, etag: etagOf(response) ?? info.etag };
}
async function draftOf(response: Response): Promise<DraftResponse> {
  const draft = decodeDraft(await readJson(response));
  if (!draft) throw malformed(response);
  return { draft, etag: etagOf(response) ?? draft.etag };
}
async function sceneOf(response: Response): Promise<SceneResponse> {
  return { scene: await readText(response), etag: etagOf(response) ?? "" };
}

const base = (id: string) => `/api/diagrams/${encodeURIComponent(id)}`;
const sceneHeaders = (extra: Record<string, string> = {}) => ({
  "Content-Type": SCENE_TYPE,
  ...extra,
});
const jsonHeaders = (extra: Record<string, string> = {}) => ({
  "Content-Type": "application/json",
  ...extra,
});

/** Raw scene URL; also the "original" download link. */
export function diagramContentUrl(
  id: string,
  options: { revision?: string; download?: boolean } = {},
): string {
  const query = new URLSearchParams();
  if (options.revision) query.set("revision", options.revision);
  if (options.download) query.set("download", "true");
  const q = query.toString();
  return `${base(id)}/content${q ? `?${q}` : ""}`;
}

export async function createDiagram(
  id: string,
  body: { name: string; folder_id: string | null; scene: unknown },
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<InfoResponse> {
  return infoOf(
    await send(base(id), {
      method: "PUT",
      headers: jsonHeaders({
        "If-None-Match": "*",
        "Idempotency-Key": idempotencyKey,
      }),
      body: JSON.stringify(body),
      signal,
    }),
  );
}
export async function getDiagram(
  id: string,
  signal?: AbortSignal,
): Promise<InfoResponse> {
  return infoOf(await send(base(id), { signal }));
}
export async function getContent(
  id: string,
  revision?: string,
  signal?: AbortSignal,
): Promise<SceneResponse> {
  return sceneOf(await send(diagramContentUrl(id, { revision }), { signal }));
}
export async function saveRevision(
  id: string,
  scene: string,
  sourceEtag: string,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<InfoResponse> {
  return infoOf(
    await send(`${base(id)}/revisions`, {
      method: "POST",
      headers: sceneHeaders({
        "If-Match": sourceEtag,
        "Idempotency-Key": idempotencyKey,
      }),
      body: scene,
      signal,
    }),
  );
}
export async function listRevisions(
  id: string,
  options: { before?: number | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<RevisionPage> {
  const query = new URLSearchParams();
  if (options.before != null) query.set("before", String(options.before));
  if (options.limit != null) query.set("limit", String(options.limit));
  const q = query.toString();
  const response = await send(`${base(id)}/revisions${q ? `?${q}` : ""}`, {
    signal,
  });
  const value = await readJson(response);
  if (!object(value) || !Array.isArray(value.items)) throw malformed(response);
  const items = value.items.map(decodeRevision);
  if (items.some((item) => !item)) throw malformed(response);
  return {
    items: items as RevisionInfo[],
    next: num(value.next) ? value.next : null,
  };
}
export async function restoreRevision(
  id: string,
  revisionId: string,
  sourceEtag: string,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<InfoResponse> {
  return infoOf(
    await send(`${base(id)}/restore`, {
      method: "POST",
      headers: jsonHeaders({
        "If-Match": sourceEtag,
        "Idempotency-Key": idempotencyKey,
      }),
      body: JSON.stringify({ revision_id: revisionId }),
      signal,
    }),
  );
}
/** `scene` null copies the current revision into the new draft. */
export async function createDraft(
  id: string,
  scene: string | null,
  sourceEtag: string,
  signal?: AbortSignal,
): Promise<DraftResponse> {
  return draftOf(
    await send(`${base(id)}/drafts`, {
      method: "POST",
      headers: sceneHeaders({ "If-Match": sourceEtag }),
      body: scene ?? undefined,
      signal,
    }),
  );
}
export async function getDraft(
  id: string,
  draftId: string,
  signal?: AbortSignal,
): Promise<DraftResponse> {
  return draftOf(
    await send(`${base(id)}/drafts/${encodeURIComponent(draftId)}`, {
      signal,
    }),
  );
}
export async function getDraftContent(
  id: string,
  draftId: string,
  signal?: AbortSignal,
): Promise<SceneResponse> {
  return sceneOf(
    await send(`${base(id)}/drafts/${encodeURIComponent(draftId)}/content`, {
      signal,
    }),
  );
}
export async function updateDraft(
  id: string,
  draftId: string,
  scene: string,
  draftEtag: string,
  signal?: AbortSignal,
): Promise<DraftResponse> {
  return draftOf(
    await send(`${base(id)}/drafts/${encodeURIComponent(draftId)}`, {
      method: "PUT",
      headers: sceneHeaders({ "If-Match": draftEtag }),
      body: scene,
      signal,
    }),
  );
}
export async function commitDraft(
  id: string,
  draftId: string,
  etags: { source: string; draft: string },
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<InfoResponse> {
  return infoOf(
    await send(`${base(id)}/drafts/${encodeURIComponent(draftId)}/commit`, {
      method: "POST",
      headers: jsonHeaders({
        "If-Match": etags.draft,
        "Idempotency-Key": idempotencyKey,
      }),
      body: JSON.stringify({ source_etag: etags.source }),
      signal,
    }),
  );
}
export async function discardDraft(
  id: string,
  draftId: string,
  draftEtag: string,
  signal?: AbortSignal,
): Promise<void> {
  await discard(
    await send(`${base(id)}/drafts/${encodeURIComponent(draftId)}`, {
      method: "DELETE",
      headers: { "If-Match": draftEtag },
      signal,
    }),
  );
}
/** Stored result of an earlier create/save/restore/commit. */
export async function getOperation(
  id: string,
  operationId: string,
  signal?: AbortSignal,
): Promise<DiagramInfo> {
  const response = await send(
    `${base(id)}/operations/${encodeURIComponent(operationId)}`,
    { signal },
  );
  const info = decodeInfo(await readJson(response));
  if (!info) throw malformed(response);
  return info;
}

/** SHA-256 (hex) of the UTF-8 bytes of `text`; null where Web Crypto is unavailable. */
export async function sha256Hex(text: string): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(text),
    );
    return Array.from(new Uint8Array(digest), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join("");
  } catch {
    return null;
  }
}
