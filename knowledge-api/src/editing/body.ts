import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { requireAction } from "../auth";
import { ApiError, MAX_JSON, type Principal } from "../contracts";
import { sha256 } from "./hash";
import type { DocumentEditing } from "./service";

/** Current-revision body read with a post-I/O authorization and revision re-check. */
export async function readBody(
  s: DocumentEditing,
  p: Principal,
  sourceId: string,
  revisionId?: string,
) {
  requireAction(p, "read");
  const source = s.store.getSource(sourceId, p);
  if (revisionId && source.current_revision !== revisionId)
    throw new ApiError(409, "source_changed");
  const revision = s.store.revision(source.current_revision);
  if (!revision) throw new ApiError(404, "source_not_found");
  let body: string;
  const markdown = [".md", ".markdown"].includes(revision.suffix);
  if (markdown) {
    const bytes = await readFile(
      join(s.options.artifactRoot, "sources", sourceId, revision.id, `original${revision.suffix}`),
    );
    if (sha256(bytes) !== revision.sha256) throw new ApiError(500, "source_integrity_failed");
    body = bytes.toString("utf8");
    if (!Buffer.from(body).equals(bytes)) throw new ApiError(422, "invalid_utf8");
  } else {
    if (!revision.fts_ready) throw new ApiError(409, "body_not_ready");
    body = s.store.db
      .query<{ body: string }, string>(
        "SELECT body FROM contexts WHERE source_revision=? ORDER BY rowid",
      )
      .all(revision.id)
      .map((row) => JSON.parse(row.body).text)
      .join("\n\n");
  }
  const current = s.store.getSource(sourceId, s.principal(p, "read"));
  if (current.current_revision !== revision.id) throw new ApiError(409, "source_changed");
  const result = {
    ...s.document(current),
    body,
    content_kind: markdown ? ("original_markdown" as const) : ("extracted_text" as const),
    evidence_revision: revision.evidence_revision,
  };
  if (Buffer.byteLength(JSON.stringify(result)) > MAX_JSON - 256)
    throw new ApiError(413, "body_too_large");
  return result;
}
