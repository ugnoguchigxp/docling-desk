import { useCallback, useEffect, useRef } from "react";
import { DRAFT_IDLE_MS, DRAFT_INTERVAL_MS } from "../../diagram/draft-timing";
import {
  DiagramRequestError,
  createDraft,
  discardDraft,
  existingDraftId,
  getDraft,
  getDraftContent,
  updateDraft,
  type DraftInfo,
} from "../../lib/diagrams";
import { classify, isPermanent, time } from "./problems";
import type { DraftState, Session, Ui } from "./session";
import { clearStash, writeStash } from "./stash";

type Sent = DraftInfo & { etag: string };

/**
 * Discard the draft this editor owns. A 412 means the draft moved on (for
 * example an upload whose response was lost): fetch its ETag and try once more.
 * A draft that is already gone counts as discarded.
 */
export async function discardOwnDraft(id: string, draft: DraftState) {
  const gone = (error: unknown) =>
    error instanceof DiagramRequestError && error.status === 404;
  try {
    await discardDraft(id, draft.id, draft.etag);
  } catch (error) {
    if (gone(error)) return;
    if (!(error instanceof DiagramRequestError) || error.status !== 412)
      throw error;
    try {
      const fresh = await getDraft(id, draft.id);
      await discardDraft(id, draft.id, fresh.etag);
    } catch (again) {
      if (!gone(again)) throw again;
    }
  }
}

/** Autosave of the editing scene into the server-side draft. */
export function useDraftSync(s: Session, ui: Ui) {
  const scheduleRef = useRef<() => void>(() => undefined);

  const sendDraft = useCallback(
    async (scene: string): Promise<Sent> => {
      const base = s.info;
      if (!base) throw new Error("図表が読み込まれていません。");
      const existing = s.draft;
      if (existing) {
        try {
          const result = await updateDraft(
            s.id,
            existing.id,
            scene,
            existing.etag,
          );
          return { ...result.draft, etag: result.etag };
        } catch (error) {
          // A 412 may only mean an earlier upload whose response was lost
          // already advanced the draft: when the stored draft is what we sent,
          // continue from its ETag instead of reporting a conflict.
          if (!(error instanceof DiagramRequestError) || error.status !== 412)
            throw error;
          const stored = await getDraftContent(s.id, existing.id).catch(
            () => null,
          );
          if (
            !stored?.etag ||
            (stored.scene !== scene && !s.attempts.includes(stored.scene))
          )
            throw error;
          if (stored.scene === scene) {
            const { draft } = await getDraft(s.id, existing.id);
            return { ...draft, etag: stored.etag };
          }
          const result = await updateDraft(
            s.id,
            existing.id,
            scene,
            stored.etag,
          );
          return { ...result.draft, etag: result.etag };
        }
      }
      try {
        const result = await createDraft(s.id, scene, base.etag);
        return { ...result.draft, etag: result.etag };
      } catch (error) {
        // A draft we do not know of exists (another tab, or our own whose
        // response was lost): reuse it when it is based on this revision,
        // otherwise replace it.
        const foreign = existingDraftId(error);
        if (!foreign) throw error;
        const found = await getDraft(s.id, foreign);
        if (found.draft.base_revision === base.revision_id) {
          const result = await updateDraft(s.id, foreign, scene, found.etag);
          return { ...result.draft, etag: result.etag };
        }
        await discardDraft(s.id, foreign, found.etag);
        const result = await createDraft(s.id, scene, base.etag);
        return { ...result.draft, etag: result.etag };
      }
    },
    [s],
  );

  const uploadDraft = useCallback(
    async (scene: string) => {
      s.attempts = [...s.attempts.slice(-4), scene];
      const result = await sendDraft(scene);
      s.draft = { id: result.id, etag: result.etag };
      s.lastDraft = scene;
      s.attempts = [];
      ui.hasDraft(true);
    },
    [s, sendDraft, ui],
  );

  const flushDraft = useCallback((): Promise<boolean> => {
    if (s.flight) return s.flight;
    const scene = s.current;
    if (
      scene == null ||
      scene === s.lastDraft ||
      scene === s.halted ||
      s.saving ||
      s.paused ||
      s.problem?.kind === "conflict"
    )
      return Promise.resolve(true);
    s.lastDraftAt = Date.now();
    const run = (async () => {
      try {
        await uploadDraft(scene);
        ui.draftNote(`下書きを自動保存しました（${time(new Date())}）`);
        return true;
      } catch (error) {
        const issue = classify(error, "draft");
        if (isPermanent(error)) {
          // Retrying the same content cannot succeed: wait for an edit.
          s.halted = scene;
          ui.raise(issue);
        } else if (issue.kind === "conflict") ui.raise(issue);
        else ui.draftNote("下書きを自動保存できませんでした。");
        return false;
      } finally {
        s.flight = null;
        if (s.current !== s.lastDraft) scheduleRef.current();
      }
    })();
    s.flight = run;
    return run;
  }, [s, uploadDraft, ui]);

  scheduleRef.current = () => {
    window.clearTimeout(s.timer);
    if (
      s.unmounted ||
      s.paused ||
      s.problem?.kind === "conflict" ||
      s.current === s.halted
    )
      return;
    const wait = Math.max(
      DRAFT_IDLE_MS,
      s.lastDraftAt + DRAFT_INTERVAL_MS - Date.now(),
    );
    s.timer = window.setTimeout(() => void flushDraft(), wait);
  };
  const schedule = useCallback(() => scheduleRef.current(), []);

  /** The canvas reported a new scene. */
  const onSceneChange = useCallback(
    (scene: string) => {
      s.current = scene;
      if (scene !== s.halted) s.halted = null;
      if (!s.seenFirst) {
        // Canvas reports its restored initial state once after mount.
        s.seenFirst = true;
        s.lastDraft = s.rescued ? null : scene;
        if (!s.unsaved) s.baseline = scene;
        if (s.rescued) schedule();
        return;
      }
      ui.dirty(s.unsaved || scene !== s.baseline);
      if (scene !== s.lastDraft) schedule();
    },
    [s, ui, schedule],
  );

  useEffect(() => {
    s.unmounted = false;
    const leave = () => {
      const scene = s.current;
      if (scene == null) return;
      const differs = s.unsaved || scene !== s.baseline;
      const synced = scene === s.lastDraft && !s.problem;
      // Input the server has not (or cannot) take is kept for this tab.
      if (differs && !synced)
        writeStash(s.id, scene, s.info?.revision_id ?? null);
      if (scene !== s.lastDraft)
        void flushDraft().then(() => {
          if (s.current === s.lastDraft && !s.problem) clearStash(s.id);
        });
    };
    const hidden = () => {
      if (document.visibilityState === "hidden") leave();
    };
    document.addEventListener("visibilitychange", hidden);
    return () => {
      document.removeEventListener("visibilitychange", hidden);
      window.clearTimeout(s.timer);
      leave();
      // From here on nothing is shown or rescheduled.
      s.unmounted = true;
    };
  }, [s, flushDraft]);

  return { uploadDraft, flushDraft, schedule, onSceneChange };
}
