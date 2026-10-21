import { useCallback, useEffect, useRef } from "react";

/**
 * What an attempt may still show. Calling it says whether the attempt is the
 * latest one in a live scope; `signal` aborts when that stops being true.
 */
export interface Attempt {
  (): boolean;
  readonly signal: AbortSignal;
}

/**
 * Feedback from an async action belongs to the feature state that started it:
 * an attempt expires when `resetKey` changes (another document, dialog or
 * page), when the screen unmounts, or when a later attempt starts.
 *
 * `signal` is for reads and other work that can simply be dropped. Do not pass
 * it to a request the server may already have accepted (a save, an upload):
 * leaving the screen does not cancel it, so its outcome has to be reported
 * through the data that outlives the screen (a reloaded list), and a network
 * abort must never be shown as "the save was cancelled".
 */
export function useAsyncScope(resetKey: unknown) {
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const invalidate = useCallback(() => {
    version.current++;
    controller.current?.abort();
    controller.current = null;
  }, []);
  useEffect(() => {
    invalidate();
    return invalidate;
  }, [resetKey, invalidate]);
  const start = useCallback((): Attempt => {
    const attempt = ++version.current;
    controller.current?.abort();
    const own = (controller.current = new AbortController());
    return Object.assign(() => version.current === attempt, {
      signal: own.signal,
    });
  }, []);
  return { start, invalidate };
}
