import {
  targetRoute,
  targetUrl,
  type Route,
  type SourceTarget,
} from "../../lib/route";
import type { Hit } from "./types";

/** The source a hit or citation points at (the shared reference, R05). */
export function hitTarget(hit: Hit): SourceTarget {
  return hit.kind === "wiki"
    ? { kind: "wiki", source: hit.source_id, section: hit.locator.anchor }
    : { kind: "document", job: hit.job_id ?? "", unit: hit.unit };
}

/**
 * Names one link in the result list (a hit, or a citation in the answer) so
 * the list can put focus back on it. Citations and hits can share a chunk.
 */
export function hitKey(hit: Hit, citation?: string) {
  return citation ? `cite:${citation}:${hit.chunk_id}` : `hit:${hit.chunk_id}`;
}

/** Where a search hit or answer citation opens, keeping the rest of the route. */
export function hitRoute(route: Route, hit: Hit): Route {
  return targetRoute(route, hitTarget(hit));
}

/** The same place as a real link, for new tab / copy link. */
export function hitUrl(hit: Hit) {
  return targetUrl(hitTarget(hit));
}

export function searchSnippet(text: string, query: string) {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= 300) return compact;
  const term = query
    .trim()
    .split(/\s+/)
    .find((word) => compact.toLowerCase().includes(word.toLowerCase()));
  const match = term ? compact.toLowerCase().indexOf(term.toLowerCase()) : 0;
  const start = Math.max(0, match - 80);
  return `${start ? "…" : ""}${compact.slice(start, start + 300)}${start + 300 < compact.length ? "…" : ""}`;
}
