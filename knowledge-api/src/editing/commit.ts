import { randomUUID } from "node:crypto";
import { mkdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { authorized } from "../auth";
import { ApiError, MAX_JSON, type Metadata, type Principal } from "../contracts";
import { checkpoint } from "../quality";
import { nowISO } from "../store";
import { sha256 } from "./hash";
import type { DocumentEditing } from "./service";

/** Version commit: new Markdown source or draft commit, one revision per call. */
export async function save(
  s: DocumentEditing,
  initial: Principal,
  body: string,
  metadata: Metadata,
  key: string | null,
  draftId?: string,
  supplied?: string | null,
  baseEtag?: string | null,
) {
  let p = s.principal(initial, "write");
  const d = draftId ? s.draft(p, draftId) : undefined;
  const operation = s.operation(p, d ? "commit" : "create", draftId, key),
    requestHash = sha256(JSON.stringify([body, metadata, supplied ?? null, baseEtag ?? null]));
  const replay = s.replay(p, operation, requestHash);
  if (replay) return replay;
  if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
  if (d) {
    s.match(d, supplied ?? null);
    if (baseEtag !== d.base_etag) throw new ApiError(412, "precondition_failed");
  }
  const existing = d?.base_revision ? s.store.getSource(d.source_id, p) : undefined;
  if (existing) s.store.match(existing, baseEtag ?? null);
  const sourceId = d?.source_id ?? randomUUID(),
    revisionId = randomUUID();
  // Reserve room for the complete body/status response after indexing as
  // well as the small save receipt. Saving an unreadable body is not success.
  const bodyResponse = {
    request_id: randomUUID(),
    source_id: sourceId,
    source_revision: revisionId,
    etag: `"${sourceId}:9007199254740991"`,
    sha256: sha256(body),
    body,
    metadata,
    content_kind: "original_markdown",
    evidence_revision: revisionId,
    status: {
      saved: true,
      search: "complete",
      fts_ready: true,
      semantic_ready: false,
      semantic_configured: true,
      evidence_revision: revisionId,
      error_code: "embedding_failed",
    },
  };
  if (Buffer.byteLength(JSON.stringify(bodyResponse)) > MAX_JSON - 256)
    throw new ApiError(413, "body_too_large");
  const folder = join(s.options.artifactRoot, "sources", sourceId, revisionId);
  const releaseSpace = await s.admission.reserve(Buffer.byteLength(body));
  let registered = false;
  try {
    await mkdir(folder, { recursive: true });
    const file = Bun.file(join(folder, "original.md"));
    await Bun.write(file, body);
    const fd = await import("node:fs/promises").then((fs) =>
      fs.open(join(folder, "original.md"), "r"),
    );
    await fd.sync();
    await fd.close();
    checkpoint("original_written");
    p = s.principal(p, "write");
    if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
    const result = s.store.tx(() => {
      const repeated = s.replay(p, operation, requestHash);
      if (repeated) return repeated;
      if (d) {
        s.match(s.draft(p, d.draft_id), supplied ?? null);
        if (existing) s.store.match(s.store.getSource(sourceId, p), baseEtag ?? null);
      }
      if (existing) s.checkExternalKey(existing, metadata);
      const saved = s.store.register(
        p,
        metadata,
        "document.md",
        ".md",
        sha256(body),
        sourceId,
        revisionId,
        existing,
        baseEtag ?? undefined,
        { profile: "local-v1" },
        false,
      );
      s.store.db.run(
        "UPDATE sources SET title=?,collection_id=?,project_id=?,region=?,source_kind=?,language=?,external_key=? WHERE id=?",
        [
          metadata.title,
          metadata.collection_id,
          metadata.project_id ?? null,
          metadata.region ?? null,
          metadata.source_kind,
          metadata.language,
          metadata.external_key ?? null,
          sourceId,
        ],
      );
      s.store.db.run("INSERT INTO revision_metadata VALUES(?,?)", [
        revisionId,
        JSON.stringify(metadata),
      ]);
      if (d) {
        for (const aid of d.attachments) {
          s.attachment(p, sourceId, aid);
          s.store.db.run("INSERT INTO revision_attachments VALUES(?,?)", [revisionId, aid]);
        }
        s.store.db.run(
          "UPDATE drafts SET state='committed',committed_revision=?,version=version+1,updated_by=?,updated_at=? WHERE id=?",
          [revisionId, p.subject, nowISO(), d.draft_id],
        );
      }
      const source = s.store.getSource(sourceId, p);
      const result = {
        ...saved,
        source_etag: s.store.etag(source),
        sha256: sha256(body),
        operation_id: operation,
        ...(d ? { draft_id: d.draft_id, draft_version: d.version } : {}),
        status: s.state(source),
      };
      s.remember(p, operation, requestHash, d ? "commit" : "create", draftId, result, {
        source_revision: d?.base_revision ?? null,
        draft_version: d?.version ?? null,
        sha256: d?.sha256 ?? null,
      });
      registered = true;
      return result;
    });
    checkpoint("editing_saved");
    return result;
  } finally {
    releaseSpace();
    if (!registered) await rm(folder, { recursive: true, force: true });
  }
}
