import { randomUUID } from "node:crypto";
import { mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import type { z } from "zod";
import { ApiError, MAX_FILE, type Principal } from "../contracts";
import { checkpoint } from "../quality";
import { nowISO } from "../store";
import type { AttachmentRow, attachSchema } from "./contract";
import { sha256 } from "./hash";
import type { DocumentEditing } from "./service";

export function attachment(
  s: DocumentEditing,
  p: Principal,
  sourceId: string,
  attachmentId: string,
) {
  const row = s.store.db
    .query<AttachmentRow, string>("SELECT * FROM attachments WHERE id=?")
    .get(attachmentId);
  if (!row || row.source_id !== sourceId) throw new ApiError(404, "attachment_not_found");
  const source = s.store.source(sourceId);
  if (source) s.store.getSource(sourceId, p);
  else s.draft(p, row.draft_id);
  return {
    attachment_id: row.id,
    source_id: row.source_id,
    draft_id: row.draft_id,
    filename: row.filename,
    media_type: row.media_type,
    size: row.size,
    sha256: row.sha256,
    reference: `knowledge-attachment:${row.id}`,
  };
}
export async function attachmentBytes(
  s: DocumentEditing,
  p: Principal,
  sourceId: string,
  attachmentId: string,
) {
  const attachment = s.attachment(p, sourceId, attachmentId);
  const bytes = await readFile(
    join(s.options.artifactRoot, "sources", sourceId, "attachments", attachmentId, "original"),
  );
  if (bytes.length !== attachment.size || sha256(bytes) !== attachment.sha256)
    throw new ApiError(500, "attachment_integrity_failed");
  s.attachment(s.principal(p, "read"), sourceId, attachmentId);
  return { attachment, bytes };
}
export async function attach(
  s: DocumentEditing,
  initial: Principal,
  draftId: string,
  input: z.infer<typeof attachSchema>,
  supplied: string | null,
  key: string | null,
  binary?: Uint8Array,
) {
  let p = s.principal(initial, "write");
  const d = s.draft(p, draftId);
  const bytes = binary ? Buffer.from(binary) : Buffer.from(input.body_base64, "base64");
  if (!binary && bytes.toString("base64") !== input.body_base64)
    throw new ApiError(422, "invalid_base64");
  if (!bytes.length || bytes.length > MAX_FILE) throw new ApiError(413, "invalid_file_size");
  const operation = s.operation(p, "attachment", draftId, key),
    requestHash = sha256(
      JSON.stringify([input.filename, input.media_type, sha256(bytes), supplied]),
    );
  const replay = s.replay(p, operation, requestHash);
  if (replay) return replay;
  s.match(d, supplied);
  if (d.attachments.length >= 100) throw new ApiError(422, "attachment_limit_reached");
  const releaseSpace = await s.admission.reserve(bytes.length);
  const aid = randomUUID(),
    folder = join(s.options.artifactRoot, "sources", d.source_id, "attachments", aid);
  let registered = false;
  try {
    await mkdir(folder, { recursive: true });
    await Bun.write(join(folder, "original"), bytes);
    const fd = await import("node:fs/promises").then((fs) =>
      fs.open(join(folder, "original"), "r"),
    );
    try {
      await fd.sync();
    } finally {
      await fd.close();
    }
    checkpoint("attachment_written");
    p = s.principal(p, "write");
    const result = s.store.tx(() => {
      const repeated = s.replay(p, operation, requestHash);
      if (repeated) return repeated;
      s.match(s.draft(p, draftId), supplied);
      s.store.db.run("INSERT INTO attachments VALUES(?,?,?,?,?,?,?)", [
        aid,
        d.source_id,
        draftId,
        input.filename,
        input.media_type,
        bytes.length,
        sha256(bytes),
      ]);
      s.store.db.run(
        "UPDATE drafts SET attachments=?,version=version+1,updated_by=?,updated_at=? WHERE id=?",
        [JSON.stringify([...d.attachments, aid]), p.subject, nowISO(), draftId],
      );
      const result = {
        ...s.attachment(p, d.source_id, aid),
        etag: s.draft(p, draftId).etag,
      };
      s.remember(p, operation, requestHash, "attachment", draftId, result, {
        draft_version: d.version,
      });
      checkpoint("attachment_before_commit");
      return result;
    });
    registered = true;
    checkpoint("attachment_saved");
    return result;
  } finally {
    releaseSpace();
    if (!registered) await rm(folder, { recursive: true, force: true });
  }
}
