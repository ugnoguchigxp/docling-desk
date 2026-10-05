import { useCallback, useEffect, useRef } from "react";

// Feedback from an async action belongs to the feature state that started it.
export function useAsyncScope(resetKey: unknown) {
  const version = useRef(0);
  const invalidate = useCallback(() => {
    version.current++;
  }, []);
  useEffect(() => {
    invalidate();
    return invalidate;
  }, [resetKey, invalidate]);
  const start = useCallback(() => {
    const attempt = ++version.current;
    return () => version.current === attempt;
  }, []);
  return { start, invalidate };
}
