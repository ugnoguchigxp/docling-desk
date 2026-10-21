import { useState } from "react";
import { itemKey, type Item } from "../../lib/types";

/**
 * Checked items, shared by the list and the gallery. Only keys are kept; the
 * selected items are always read back from the items currently shown, so a
 * filter or a refreshed snapshot cannot leave a stale target. Changing
 * `resetKey` (the folder) clears the selection while rendering, without an
 * effect that would briefly show the old selection.
 */
export function useSelection(visible: Item[], resetKey: string | null) {
  const [checked, setChecked] = useState(new Set<string>()),
    [seenKey, setSeenKey] = useState(resetKey);
  if (seenKey !== resetKey) {
    setSeenKey(resetKey);
    setChecked(new Set());
  }
  const selected = visible.filter((i) => checked.has(itemKey(i)));
  return {
    selected,
    isChecked: (item: Item) => checked.has(itemKey(item)),
    toggle: (item: Item, value: boolean) =>
      setChecked((previous) => {
        const next = new Set(previous);
        if (value) next.add(itemKey(item));
        else next.delete(itemKey(item));
        return next;
      }),
    selectAll: () => setChecked(new Set(visible.map(itemKey))),
    clear: () => setChecked(new Set()),
  };
}
export type Selection = ReturnType<typeof useSelection>;
