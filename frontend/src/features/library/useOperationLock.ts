import { useRef, useState } from "react";

/**
 * One operation at a time for the whole library (folder operations, drops,
 * diagram creation). The ref answers synchronously for double submits; the
 * state is what disables the controls.
 */
export function useOperationLock() {
  const active = useRef(false),
    [busy, setBusy] = useState(false);
  return {
    busy,
    isBusy: () => active.current,
    /** False when another operation holds the lock. */
    acquire() {
      if (active.current) return false;
      active.current = true;
      setBusy(true);
      return true;
    },
    release() {
      active.current = false;
      setBusy(false);
    },
  };
}
export type OperationLock = ReturnType<typeof useOperationLock>;
