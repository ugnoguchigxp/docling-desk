import { useState } from "react";
import type { Layout, Scope } from "./entries";

const STORAGE_KEY = "docling-library-views";
function readPreferences() {
  try {
    const saved: unknown = JSON.parse(
      localStorage.getItem(STORAGE_KEY) || "{}",
    );
    const result: Record<string, Layout> = {};
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      for (const [key, value] of Object.entries(saved)) {
        if (value === "list" || value === "gallery") result[key] = value;
      }
    }
    return result;
  } catch {
    return {};
  }
}
/**
 * Display condition. The route's scope and layout win; without them the scope
 * is kept locally and the layout comes from the per-folder preference saved in
 * browser storage (optional).
 */
export function useLibraryView({
  folder,
  scope: requestedScope,
  layout: requestedLayout,
  onScope,
  onLayout,
}: {
  folder: string | null;
  scope?: Scope;
  layout?: Layout;
  onScope?: (scope: Scope) => void;
  onLayout?: (layout: Layout) => void;
}) {
  const [localScope, setLocalScope] = useState<Scope>("folder"),
    [preferences, setPreferences] = useState(readPreferences);
  const scope = requestedScope || localScope,
    preferenceKey = `${scope}:${folder || "root"}`,
    layout =
      requestedLayout ||
      preferences[preferenceKey] ||
      (scope === "images" ? "gallery" : "list");
  return {
    scope,
    layout,
    changeLayout(next: Layout) {
      const updated = { ...preferences, [preferenceKey]: next };
      setPreferences(updated);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      } catch {
        /* Browser storage is optional. */
      }
      onLayout?.(next);
    },
    changeScope(next: Scope) {
      setLocalScope(next);
      onScope?.(next);
    },
  };
}
