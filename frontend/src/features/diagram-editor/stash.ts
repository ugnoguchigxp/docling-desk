import { object } from "../../lib/value";

export interface Stash {
  scene: string;
  base: string | null;
  saved: number;
}

// Unsaved input that could not reach the server, kept for this browser tab so
// that leaving the editor does not silently lose it.
const stashKey = (id: string) => `docling-diagram-unsaved:${id}`;

export function readStash(id: string): Stash | null {
  try {
    const raw = window.sessionStorage.getItem(stashKey(id));
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (
      object(value) &&
      typeof value.scene === "string" &&
      typeof value.saved === "number"
    )
      return {
        scene: value.scene,
        base: typeof value.base === "string" ? value.base : null,
        saved: value.saved,
      };
  } catch {
    // Storage can be unavailable; the stash is only a convenience.
  }
  return null;
}
export function writeStash(id: string, scene: string, base: string | null) {
  try {
    window.sessionStorage.setItem(
      stashKey(id),
      JSON.stringify({ scene, base, saved: Date.now() / 1000 }),
    );
  } catch {
    // Quota or privacy mode: nothing more can be done.
  }
}
export function clearStash(id: string) {
  try {
    window.sessionStorage.removeItem(stashKey(id));
  } catch {
    // ignore
  }
}
