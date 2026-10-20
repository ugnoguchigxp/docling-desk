import { useCallback } from "react";
import {
  DiagramRequestError,
  commitDraft,
  getContent,
  getDiagram,
  restoreRevision,
  saveRevision,
  sha256Hex,
  type DraftInfo,
  type InfoResponse,
  type RevisionInfo,
} from "../../lib/diagrams";
import { errorText } from "../../lib/api";
import { discardOwnDraft } from "./useDraftSync";
import { classify } from "./problems";
import type { DraftState, Pending, Session, Ui } from "./session";
import { clearStash } from "./stash";

const draftMoved = (a: DraftState | null, b: DraftState | null) =>
  (a?.id ?? null) !== (b?.id ?? null) ||
  (a?.etag ?? null) !== (b?.etag ?? null);

/** Saving a revision (directly or by committing the draft) and restoring one. */
export function useDiagramSave(
  s: Session,
  ui: Ui,
  parts: {
    /** Open `scene` in a fresh canvas and reset the editing bookkeeping. */
    adopt: (info: NonNullable<Session["info"]>, scene: string) => void;
    uploadDraft: (scene: string) => Promise<void>;
    schedule: () => void;
  },
) {
  const { adopt, uploadDraft, schedule } = parts;

  /** Remove a draft the client no longer owns; report if that fails. */
  const dropStale = useCallback(
    async (draft: DraftInfo) => {
      try {
        await discardOwnDraft(s.id, { id: draft.id, etag: draft.etag });
        if (s.info?.draft?.id === draft.id) s.info = { ...s.info, draft: null };
      } catch (error) {
        ui.notice(
          `古い下書きをサーバーから破棄できませんでした。${errorText(error)}`,
        );
      }
    },
    [s, ui],
  );

  /** A save/commit succeeded for `scene`: it is now the saved baseline. */
  const settle = useCallback(
    (scene: string, result: InfoResponse) => {
      const saved = { ...result.info, etag: result.etag };
      s.pending = null;
      s.restoring = null;
      s.attempts = [];
      s.info = saved;
      s.draft = null;
      s.baseline = scene;
      s.unsaved = false;
      s.lastDraft = scene;
      s.halted = null;
      ui.info(saved);
      ui.hasDraft(false);
      ui.dirty(s.current !== scene);
      ui.draftNote("");
      ui.history();
      clearStash(s.id);
      ui.changed();
      // /revisions does not touch drafts: whatever the server still lists is
      // not ours any more.
      if (saved.draft) void dropStale(saved.draft);
    },
    [s, ui, dropStale],
  );

  const execute = useCallback(
    async (op: Pending): Promise<InfoResponse> => {
      try {
        return op.draft
          ? await commitDraft(
              s.id,
              op.draft.id,
              { source: op.sourceEtag, draft: op.draft.etag },
              op.key,
            )
          : await saveRevision(s.id, op.scene, op.sourceEtag, op.key);
      } catch (error) {
        if (!(error instanceof DiagramRequestError) || error.status !== 412)
          throw error;
        // The same content may already be saved without us knowing it: the
        // Idempotency-Key can have expired, or another session of this user
        // saved it. If the current revision is exactly what we sent, adopt it.
        const hash = await sha256Hex(op.scene);
        const latest = await getDiagram(s.id).catch(() => null);
        if (
          hash &&
          latest &&
          latest.info.sha256 === hash &&
          latest.info.etag !== op.sourceEtag
        )
          return latest;
        throw error;
      }
    },
    [s],
  );

  const save = useCallback(async () => {
    const scene = s.current;
    if (s.saving || scene == null || !s.info) return;
    s.saving = true;
    ui.busy("save");
    window.clearTimeout(s.timer);
    ui.raise(null);
    ui.notice("");
    try {
      await s.flight;
      let op = s.pending;
      if (op && (op.scene !== scene || draftMoved(op.draft, s.draft))) {
        // An earlier save may have been applied although its response was
        // lost: replay it with its own key and adopt the result first. If the
        // server says it was not applied (412/404), it is obsolete: drop it.
        try {
          settle(op.scene, await execute(op));
          if (op.scene === scene) return;
        } catch (error) {
          if (
            !(error instanceof DiagramRequestError) ||
            (error.status !== 412 && error.status !== 404)
          )
            throw error;
          s.pending = null;
        }
        op = null;
      }
      if (!op) {
        // A new request gets a new key; retries of this one reuse it.
        if (s.draft && s.lastDraft !== scene) await uploadDraft(scene);
        op = {
          key: crypto.randomUUID(),
          scene,
          sourceEtag: s.info!.etag,
          draft: s.draft,
        };
        s.pending = op;
      }
      settle(op.scene, await execute(op));
    } catch (error) {
      if (error instanceof DiagramRequestError && error.status === 409)
        s.pending = null;
      ui.raise(classify(error, "save"));
    } finally {
      s.saving = false;
      ui.busy("");
      if (s.current !== s.lastDraft) schedule();
    }
  }, [s, ui, settle, execute, uploadDraft, schedule]);

  const restore = useCallback(
    async (revision: RevisionInfo, confirmed = false) => {
      const base = s.info;
      if (!base || s.saving) return;
      if (!confirmed) {
        const dirty =
          s.unsaved || (s.current != null && s.current !== s.baseline);
        const warning =
          dirty || s.draft
            ? `編集中の内容と下書きを破棄して、版 ${revision.number} の内容で新しい版を作成します。よろしいですか？`
            : `版 ${revision.number} の内容で新しい版を作成します。よろしいですか？`;
        if (!window.confirm(warning)) return;
      }
      s.saving = true;
      ui.busy("restore");
      window.clearTimeout(s.timer);
      ui.raise(null);
      try {
        await s.flight;
        // A retry reuses the key only for the very same request.
        if (
          !s.restoring ||
          s.restoring.revision !== revision.id ||
          s.restoring.etag !== base.etag
        )
          s.restoring = {
            revision: revision.id,
            etag: base.etag,
            key: crypto.randomUUID(),
          };
        s.lastRestore = revision;
        const result = await restoreRevision(
          s.id,
          revision.id,
          base.etag,
          s.restoring.key,
        );
        s.restoring = null;
        const restored = { ...result.info, etag: result.etag };
        clearStash(s.id);
        ui.changed();
        try {
          const latest = await getContent(s.id, restored.revision_id);
          adopt(restored, latest.scene);
        } catch (error) {
          // The restore is done on the server: keep its revision/ETag so that
          // nothing is saved over it, and ask for a reload of the content.
          s.pending = null;
          s.draft = null;
          s.info = restored;
          ui.info(restored);
          ui.hasDraft(false);
          ui.history();
          ui.needsReload(true);
          ui.raise({
            kind: "failure",
            message: `版 ${revision.number} は復元されましたが、内容を読み込めませんでした。${errorText(error)}`,
            retry: false,
            reload: true,
          });
        }
        // The restore leaves any earlier draft behind; it is stale now.
        if (restored.draft) void dropStale(restored.draft);
      } catch (error) {
        if (error instanceof DiagramRequestError && error.status === 409)
          s.restoring = null;
        ui.raise(classify(error, "restore"));
      } finally {
        s.saving = false;
        ui.busy("");
      }
    },
    [s, ui, adopt, dropStale],
  );

  return { save, restore };
}
