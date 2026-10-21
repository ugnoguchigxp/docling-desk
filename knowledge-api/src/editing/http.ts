import type { Hono } from "hono";
import type { UploadAdmission } from "../admission";
import type { ApiOptions, Env } from "../app";
import { authorized, requireAction } from "../auth";
import { ApiError, id, MAX_FILE, MAX_JSON, metadataSchema, parse } from "../contracts";
import type { SourceRow } from "../store";
import {
  attachSchema,
  commitDraftSchema,
  createDraftSchema,
  createMarkdownSchema,
  documentListParameters,
  EDITING_ENDPOINTS,
  lookupSchema,
  patchDraftSchema,
  updateDraftSchema,
} from "./contract";
import { DocumentEditing } from "./service";

/** Route wiring: authorization, admission slots and post-I/O re-checks stay in the shared paths. */
export function installEditing(
  app: Hono<Env>,
  options: ApiOptions,
  json: (request: Request) => Promise<unknown>,
  boundedBody: (request: Request, max: number) => Promise<Buffer>,
  admission: UploadAdmission,
) {
  const service = new DocumentEditing(options, admission),
    store = options.store;
  for (const endpoint of EDITING_ENDPOINTS) {
    const path = endpoint.path.replace(/\{(\w+)\}/g, ":$1");
    if (endpoint.method === "post" && endpoint.path.endsWith("/attachments")) {
      app.post(path, async (c, next) => {
        requireAction(c.get("principal"), "write");
        const release = admission.acquire();
        try {
          await next();
        } finally {
          release();
        }
      });
    }
    app.on(endpoint.method.toUpperCase(), path, async (c) => {
      const action = "write" in endpoint && endpoint.write ? "write" : "read";
      let p = service.principal(c.get("principal"), action);
      const readJSON = async () => {
        const input = await json(c.req.raw);
        p = service.principal(p, action);
        return input;
      };
      const sourceId = c.req.param("source_id") ?? "",
        draftId = c.req.param("draft_id") ?? "";
      const key = c.req.header("idempotency-key") ?? null,
        condition = c.req.header("if-match") ?? null;
      let result: Record<string, unknown>;
      if (endpoint.path === "/api/v1/scopes") result = { scopes: p.grants, actions: p.actions };
      else if (
        endpoint.method === "get" &&
        (endpoint.path === "/api/v1/sources" || endpoint.path === "/api/v1/drafts")
      ) {
        const query = c.req.query();
        const offset = parse(
          documentListParameters.offset,
          query.offset === undefined ? undefined : Number(query.offset),
        );
        const limit = parse(
          documentListParameters.limit,
          query.limit === undefined ? undefined : Number(query.limit),
        );
        const sourceFilter = c.req.query("source_id");
        if (sourceFilter) parse(documentListParameters.source_id, sourceFilter);
        const rows =
          endpoint.path === "/api/v1/sources"
            ? store.db
                .query<SourceRow, []>(
                  "SELECT * FROM sources WHERE deleted=0 ORDER BY created_at,id",
                )
                .all()
                .filter((s) => authorized(p, s))
                .map((s) => service.document(s))
            : store.db
                .query<{ id: string }, []>("SELECT id FROM drafts ORDER BY created_at,id")
                .all()
                .flatMap((row) => {
                  try {
                    const d = service.draft(p, row.id);
                    if (sourceFilter && d.source_id !== sourceFilter) return [];
                    const { body: _, ...info } = d;
                    return [info];
                  } catch (e) {
                    if (e instanceof ApiError && e.status === 404) return [];
                    throw e;
                  }
                });
        result = {
          [endpoint.path === "/api/v1/sources" ? "documents" : "drafts"]: rows.slice(
            offset,
            offset + limit,
          ),
          next_offset: rows.length > offset + limit ? offset + limit : null,
        };
      } else if (endpoint.path.endsWith("/body"))
        result = await service.body(p, parse(id, sourceId), c.req.query("revision"));
      else if (endpoint.path === "/api/v1/markdown-sources") {
        const input = parse(createMarkdownSchema, await readJSON());
        result = await service.save(p, input.body, input.metadata, key);
      } else if (endpoint.path === "/api/v1/drafts" && endpoint.method === "post")
        result = await service.createDraft(p, parse(createDraftSchema, await readJSON()), key);
      else if (endpoint.path === "/api/v1/drafts/{draft_id}") {
        parse(id, draftId);
        if (endpoint.method === "get") result = service.draft(p, draftId);
        else
          result = service.updateDraft(
            p,
            draftId,
            endpoint.method === "put"
              ? parse(updateDraftSchema, await readJSON())
              : parse(patchDraftSchema, await readJSON()),
            condition,
            key,
            endpoint.method === "patch",
          );
      } else if (endpoint.path.endsWith("/commit")) {
        const input = parse(commitDraftSchema, await readJSON());
        const d = service.draft(p, parse(id, draftId));
        result = await service.save(
          p,
          d.body,
          d.metadata,
          key,
          draftId,
          condition,
          input.source_etag,
        );
      } else if (endpoint.path.endsWith("/metadata")) {
        const metadata = parse(metadataSchema, await readJSON());
        result = service.updateMetadata(p, sourceId, metadata, condition, key);
      } else if (endpoint.path === "/api/v1/operations/lookup") {
        const input = parse(lookupSchema, await readJSON());
        const op = service.operation(p, input.operation, input.target_id, input.key);
        const row = store.db
          .query<{ result: string }, string>(
            "SELECT result FROM idempotency WHERE key=? AND expires>strftime('%s','now')*1000",
          )
          .get(op);
        if (!row) throw new ApiError(404, "operation_unknown");
        const saved = JSON.parse(row.result);
        service.authorizeResult(p, saved);
        result = { result: saved };
      } else if (endpoint.path.endsWith("/attachments") && endpoint.method === "post") {
        parse(id, draftId);
        if (c.req.header("content-type")?.startsWith("multipart/form-data;")) {
          const raw = await boundedBody(c.req.raw, MAX_FILE + 1024 * 1024);
          const form = await new Request(c.req.url, {
            method: "POST",
            headers: c.req.raw.headers,
            body: new Uint8Array(raw),
          }).formData();
          const file = form.get("file");
          if (
            !(file instanceof File) ||
            [...form.keys()].some((k) => k !== "file") ||
            form.getAll("file").length !== 1
          )
            throw new ApiError(422, "invalid_multipart");
          const input = parse(attachSchema, {
            filename: file.name,
            media_type: file.type || "application/octet-stream",
            body_base64: "",
          });
          result = await service.attach(
            p,
            draftId,
            input,
            condition,
            key,
            new Uint8Array(await file.arrayBuffer()),
          );
        } else
          result = await service.attach(
            p,
            draftId,
            parse(attachSchema, await readJSON()),
            condition,
            key,
          );
      } else if (endpoint.path.endsWith("/attachments")) {
        const owner = draftId
          ? service.draft(p, parse(id, draftId)).source_id
          : store.getSource(parse(id, sourceId), p).id;
        const ids = draftId
          ? service.draft(p, draftId).attachments
          : store.db
              .query<{ attachment_id: string }, string>(
                "SELECT attachment_id FROM revision_attachments WHERE revision_id=?",
              )
              .all(store.getSource(owner, p).current_revision)
              .map((r) => r.attachment_id);
        result = { attachments: ids.map((aid) => service.attachment(p, owner, aid)) };
      } else {
        if (endpoint.path.startsWith("/api/v1/drafts/")) {
          const d = service.draft(p, parse(id, draftId));
          const aid = parse(id, c.req.param("attachment_id"));
          if (!d.attachments.includes(aid)) throw new ApiError(404, "attachment_not_found");
          const { attachment, bytes } = await service.attachmentBytes(p, d.source_id, aid);
          const latest = service.draft(service.principal(p, "read"), draftId);
          if (!latest.attachments.includes(aid)) throw new ApiError(404, "attachment_not_found");
          if (bytes.length > 1024 * 1024) throw new ApiError(413, "attachment_too_large_for_json");
          return c.json({
            request_id: c.get("requestId"),
            ...attachment,
            body_base64: bytes.toString("base64"),
          });
        }
        const aid = parse(id, c.req.param("attachment_id"));
        const source = store.getSource(parse(id, sourceId), p);
        if (
          !store.db
            .query("SELECT 1 FROM revision_attachments WHERE revision_id=? AND attachment_id=?")
            .get(source.current_revision, aid)
        )
          throw new ApiError(404, "attachment_not_found");
        const { attachment, bytes } = await service.attachmentBytes(p, sourceId, aid);
        const current = store.getSource(sourceId, service.principal(p, "read"));
        if (current.current_revision !== source.current_revision)
          throw new ApiError(409, "source_changed");
        if (endpoint.path.endsWith("/content")) {
          c.header("Content-Type", attachment.media_type);
          c.header("Content-Disposition", "attachment");
          return c.body(new Uint8Array(bytes));
        }
        if (bytes.length > 1024 * 1024) throw new ApiError(413, "attachment_too_large_for_json");
        result = { ...attachment, body_base64: bytes.toString("base64") };
      }
      if ("etag" in result && typeof result.etag === "string") c.header("ETag", result.etag);
      if ("source_etag" in result && typeof result.source_etag === "string")
        c.header("ETag", result.source_etag);
      const response = { request_id: c.get("requestId"), ...result };
      if (Buffer.byteLength(JSON.stringify(response)) > MAX_JSON)
        throw new ApiError(413, "response_too_large");
      return c.json(response);
    });
  }
  return service;
}
