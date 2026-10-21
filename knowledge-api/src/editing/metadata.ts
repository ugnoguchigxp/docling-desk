import { authorized } from "../auth";
import { ApiError, id, type Metadata, type Principal, parse } from "../contracts";
import { sha256 } from "./hash";
import type { DocumentEditing } from "./service";

/** In-place metadata change for an existing source; the caller has already parsed the body. */
export function updateMetadata(
  s: DocumentEditing,
  p: Principal,
  sourceId: string,
  metadata: Metadata,
  condition: string | null,
  key: string | null,
) {
  const source = s.store.getSource(parse(id, sourceId), p);
  if (!authorized(p, metadata)) throw new ApiError(403, "scope_forbidden");
  if (
    metadata.source_kind === "wiki" &&
    ![".md", ".markdown", ".txt", ".text"].includes(
      s.store.revision(source.current_revision)?.suffix ?? "",
    )
  )
    throw new ApiError(422, "wiki_text_required");
  const op = s.operation(p, "metadata", sourceId, key),
    h = sha256(JSON.stringify([metadata, condition]));
  return s.store.tx(() => {
    const replay = s.replay(p, op, h);
    if (replay) return replay;
    s.store.match(source, condition);
    s.checkExternalKey(source, metadata);
    const old = s.metadata(source);
    s.store.db.run(
      "UPDATE sources SET title=?,collection_id=?,project_id=?,region=?,source_kind=?,language=?,external_key=?,generation=generation+1 WHERE id=?",
      [
        metadata.title,
        metadata.collection_id,
        metadata.project_id ?? null,
        metadata.region ?? null,
        metadata.source_kind,
        metadata.language,
        metadata.external_key ?? null,
        source.id,
      ],
    );
    if (
      !authorized(
        {
          ...p,
          grants: [
            {
              collection_id: old.collection_id,
              project_id: old.project_id,
              region: old.region,
            },
          ],
        },
        metadata,
      )
    ) {
      s.store.db.run(
        "UPDATE jobs SET state='superseded',lease_until=0 WHERE source_id=? AND kind='extract' AND state IN ('queued','running')",
        [source.id],
      );
      s.store.db.run("UPDATE revisions SET semantic_ready=0 WHERE source_id=?", [source.id]);
      s.store.enqueue("extract", source.id, source.current_revision, { profile: "local-v1" });
    }
    s.store.invalidateAnswers(source.id);
    return s.remember(p, op, h, "metadata", sourceId, s.document(s.store.getSource(sourceId, p)), {
      source_revision: source.current_revision,
      etag: condition,
    });
  });
}
