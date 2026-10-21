import type { Library } from "./types";

/**
 * When a query asks the server again on its own.
 *
 * Three cadences, chosen from what the server is doing and not from the screen:
 *  - busy: the server is working on something the person waits for. Keep the
 *    notice delay each feature has always had (`busy` below).
 *  - watch: nothing is running; only changes made elsewhere (another tab or
 *    client) are looked for.
 *  - after a failed request the interval stretches to BACKOFF_MS instead of
 *    hammering a server that just failed.
 * No interval runs in a hidden tab (TanStack Query's default), and a request
 * is made again when the browser comes back online.
 */
export const WATCH_MS = 5000;
export const BACKOFF_MS = 10000;

interface Rule<T> {
  /** Interval while `isBusy(data)` holds. */
  busy: number;
  isBusy: (data: T) => boolean;
  /** Interval otherwise; false means no polling. */
  idle: number | false;
}

export function pollInterval<T>(
  state: { data?: T; error?: unknown },
  rule: Rule<T>,
): number | false {
  const normal =
    state.data !== undefined && rule.isBusy(state.data) ? rule.busy : rule.idle;
  return normal !== false && state.error
    ? Math.max(normal, BACKOFF_MS)
    : normal;
}

export const running = (state: string) =>
  state === "queued" || state === "running";

/** For a list that only changes elsewhere: look every WATCH_MS. */
export const watch = (state: { error?: unknown }) =>
  state.error ? BACKOFF_MS : WATCH_MS;

/**
 * The document list: two seconds while something is being processed, so a
 * finished conversion shows up as soon as it always did. Otherwise only the
 * list screen watches for changes made elsewhere.
 */
export const libraryInterval = (
  state: { data?: Library; error?: unknown },
  mode: "library" | string,
) =>
  pollInterval(state, {
    busy: 2000,
    isBusy: (data) => data.jobs.some((job) => running(job.state)),
    idle: mode === "library" ? WATCH_MS : false,
  });

/**
 * A search or answer: two seconds until the server is done. A finished result
 * is only asked about to learn that its sources were updated; a failed,
 * cancelled or already outdated one cannot change any more.
 */
export const searchInterval = (state: {
  data?: { state: string; output?: { stale?: boolean } };
  error?: unknown;
}) =>
  pollInterval(state, {
    busy: 2000,
    isBusy: (data) => running(data.state),
    idle:
      !state.data ||
      ["failed", "cancelled"].includes(state.data.state) ||
      state.data.output?.stale
        ? false
        : WATCH_MS,
  });
