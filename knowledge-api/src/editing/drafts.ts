import { randomUUID } from "node:crypto";
import type { z } from "zod";
import { authorized } from "../auth";
import { ApiError, type Principal } from "../contracts";
import { nowISO, type SourceRow } from "../store";
import type { createDraftSchema, patchDraftSchema, updateDraftSchema } from "./contract";
import { sha256 } from "./hash";
import { applyEdits } from "./patch";
import type { DocumentEditing } from "./service";

export async function createDraft(
  s: DocumentEditing,
  initial: Principal,
  input: z.infer<typeof createDraftSchema>,
  key: string | null,
) {
  let p = s.principal(initial, "write");
  const operation = s.operation(p, "draft", input.source_id, key),
    requestHash = sha256(JSON.stringify(input));
  const replay = s.replay(p, operation, requestHash);
  if (replay) return replay;
  let source: SourceRow | undefined,
    body = input.body ?? "",
    metadata = input.metadata;
  if (input.source_id) {
    source = s.store.getSource(input.source_id, p);
    s.store.match(source, input.source_etag ?? null);
    if (source.current_revision !== input.source_revision)
      throw new ApiError(409, "source_changed");
    const r = s.store.revision(source.current_revision);
    if (!r || ![".md", ".markdown"].includes(r.suffix))
      throw new ApiError(422, "markdown_required");
    if (input.body === undefined) body = (await s.body(p, source.id, source.current_revision)).body;
    metadata ??= s.metadata(source);
  } else if (input.source_etag || input.source_revision) throw new ApiError(422, "invalid_base");
  if (!metadata || !authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
  p = s.principal(p, "write");
  return s.store.tx(() => {
    const repeated = s.replay(p, operation, requestHash);
    if (repeated) return repeated;
    if (source) s.store.match(s.store.getSource(source.id, p), input.source_etag ?? null);
    const draftId = randomUUID(),
      now = nowISO();
    s.store.db.run("INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
      draftId,
      source?.id ?? randomUUID(),
      source?.current_revision ?? null,
      input.source_etag ?? null,
      Buffer.from(body),
      JSON.stringify(metadata),
      JSON.stringify(
        source
          ? s.store.db
              .query<{ attachment_id: string }, string>(
                "SELECT attachment_id FROM revision_attachments WHERE revision_id=?",
              )
              .all(source.current_revision)
              .map((row) => row.attachment_id)
          : [],
      ),
      1,
      sha256(body),
      "draft",
      null,
      p.subject,
      p.subject,
      now,
      now,
    ]);
    const result = s.draft(p, draftId);
    return s.remember(p, operation, requestHash, "draft", source?.id, result, {
      source_revision: source?.current_revision ?? null,
    });
  });
}
export function updateDraft(
  s: DocumentEditing,
  p: Principal,
  draftId: string,
  input: z.infer<typeof updateDraftSchema> | z.infer<typeof patchDraftSchema>,
  supplied: string | null,
  key: string | null,
  partial = false,
) {
  p = s.principal(p, "write");
  const d = s.draft(p, draftId);
  const operation = s.operation(p, partial ? "edit" : "update", draftId, key),
    requestHash = sha256(JSON.stringify([input, supplied]));
  return s.store.tx(() => {
    const replay = s.replay(p, operation, requestHash);
    if (replay) return replay;
    s.match(d, supplied);
    const body = "edits" in input ? applyEdits(d.body, input) : input.body;
    const metadata = "metadata" in input && input.metadata ? input.metadata : d.metadata;
    if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
    const attachments =
      "attachments" in input && input.attachments ? input.attachments : d.attachments;
    for (const aid of attachments) s.attachment(p, d.source_id, aid);
    s.store.db.run(
      "UPDATE drafts SET body=?,metadata=?,attachments=?,version=version+1,sha256=?,updated_by=?,updated_at=? WHERE id=?",
      [
        Buffer.from(body),
        JSON.stringify(metadata),
        JSON.stringify(attachments),
        sha256(body),
        p.subject,
        nowISO(),
        draftId,
      ],
    );
    return s.remember(
      p,
      operation,
      requestHash,
      partial ? "edit" : "update",
      draftId,
      s.draft(p, draftId),
      { draft_version: d.version, source_revision: d.base_revision, sha256: d.sha256 },
    );
  });
}
