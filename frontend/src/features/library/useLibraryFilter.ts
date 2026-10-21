import { useState } from "react";
import type { Filter } from "./entries";

/**
 * Search text, type and sort. The search text belongs to a folder and is
 * cleared when the folder changes (while rendering, like the selection);
 * type and sort stay.
 */
export function useLibraryFilter(resetKey: string | null) {
  const [query, setQuery] = useState(""),
    [type, setType] = useState(""),
    [sort, setSort] = useState("recent"),
    [seenKey, setSeenKey] = useState(resetKey);
  if (seenKey !== resetKey) {
    setSeenKey(resetKey);
    setQuery("");
  }
  const filter: Filter = { query, type, sort };
  return {
    filter,
    setQuery,
    setType,
    setSort,
    /** Scope change: a search for one kind of list does not carry over. */
    clear() {
      setQuery("");
      setType("");
    },
  };
}
