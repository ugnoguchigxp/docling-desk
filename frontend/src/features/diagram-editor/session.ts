import type { DiagramInfo, RevisionInfo } from "../../lib/diagrams";
import type { Problem } from "./problems";

export interface DraftState {
  id: string;
  etag: string;
}
export interface Pending {
  key: string;
  scene: string;
  sourceEtag: string;
  draft: DraftState | null;
}

/**
 * Mutable editing state shared by the editor's hooks. It lives in refs, not
 * React state, because async save/draft requests must see the latest values.
 */
export interface Session {
  id: string;
  info: DiagramInfo | null;
  /** The server-side draft this editor owns. */
  draft: DraftState | null;
  /** Latest scene reported by the canvas; null until the canvas reports. */
  current: string | null;
  /** Scene the canvas was opened with. */
  opened: string | null;
  /** Saved scene (canvas-normalized) that `current` is compared with. */
  baseline: string | null;
  /** The opened scene is itself unsaved (resumed draft / restored stash). */
  unsaved: boolean;
  rescued: boolean;
  seenFirst: boolean;
  /** Incremented per canvas instance; reports of older ones are ignored. */
  generation: number;
  /** Scene the server draft holds. */
  lastDraft: string | null;
  lastDraftAt: number;
  /** Draft uploads whose outcome is unknown (the response may have been lost). */
  attempts: string[];
  /** Scene the server refused for good (413/422): do not autosave it again. */
  halted: string | null;
  timer: number | undefined;
  flight: Promise<boolean> | null;
  saving: boolean;
  paused: boolean;
  unmounted: boolean;
  pending: Pending | null;
  restoring: { revision: string; etag: string; key: string } | null;
  lastRestore: RevisionInfo | null;
  problem: Problem | null;
}

export function newSession(id: string): Session {
  return {
    id,
    info: null,
    draft: null,
    current: null,
    opened: null,
    baseline: null,
    unsaved: false,
    rescued: false,
    seenFirst: false,
    generation: 0,
    lastDraft: null,
    lastDraftAt: 0,
    attempts: [],
    halted: null,
    timer: undefined,
    flight: null,
    saving: false,
    paused: false,
    unmounted: false,
    pending: null,
    restoring: null,
    lastRestore: null,
    problem: null,
  };
}

/** What the hooks may show; every call is a no-op once the editor unmounted. */
export interface Ui {
  raise(problem: Problem | null): void;
  notice(message: string): void;
  draftNote(message: string): void;
  hasDraft(value: boolean): void;
  dirty(value: boolean): void;
  info(info: DiagramInfo): void;
  busy(value: "" | "save" | "restore"): void;
  /** The version list is out of date. */
  history(): void;
  needsReload(value: boolean): void;
  changed(): void;
}
