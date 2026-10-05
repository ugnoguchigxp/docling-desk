import { useCallback, useEffect, useState } from "react";
import type { View } from "./types";
export const views: View[] = ["preview", "tables", "structure", "rag"];
export interface Route {
  mode: "library" | "wiki";
  source: string | null;
  section: string | null;
  unit: number | null;
  folder: string | null;
  job: string | null;
  view: View;
}
export function readRoute(): Route {
  const p = new URLSearchParams(location.search),
    v = p.get("view");
  return {
    mode:
      p.get("mode") === "wiki"
        ? "wiki"
        : p.get("mode") === "library" ||
            ["job", "folder", "view", "unit"].some((key) => p.has(key))
          ? "library"
          : "wiki",
    source: p.get("source"),
    section: p.get("section"),
    unit:
      /^\d+$/.test(p.get("unit") || "") && Number(p.get("unit")) > 0
        ? Math.min(10000, Number(p.get("unit")))
        : null,
    folder: p.get("folder"),
    job: p.get("job"),
    view: views.includes(v as View) ? (v as View) : "preview",
  };
}
export function useRoute() {
  const [route, setRoute] = useState(readRoute);
  useEffect(() => {
    const pop = () => setRoute(readRoute());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  const navigate = useCallback((next: Route, push = true) => {
    const url = new URL(location.href);
    url.search = "";
    url.hash = "";
    url.searchParams.set("mode", next.mode);
    if (next.source) url.searchParams.set("source", next.source);
    if (next.section) url.searchParams.set("section", next.section);
    if (next.unit) url.searchParams.set("unit", String(next.unit));
    if (next.folder) url.searchParams.set("folder", next.folder);
    if (next.job) {
      url.searchParams.set("job", next.job);
      url.searchParams.set("view", next.view);
    }
    if (push && url.href !== location.href) history.pushState({}, "", url);
    else if (!push) history.replaceState(history.state, "", url);
    setRoute(next);
  }, []);
  return { route, navigate };
}
