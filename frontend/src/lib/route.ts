import { useCallback, useEffect, useState } from "react";
import { withBase } from "./base";
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
  libraryScope?: "folder" | "images";
  libraryView?: "list" | "gallery";
}
export function readRoute(): Route {
  const p = new URLSearchParams(location.search),
    v = p.get("view");
  return {
    mode:
      p.get("mode") === "wiki"
        ? "wiki"
        : p.get("mode") === "library" ||
            [
              "job",
              "folder",
              "view",
              "unit",
              "library_scope",
              "library_view",
            ].some((key) => p.has(key))
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
    libraryScope: p.get("library_scope") === "images" ? "images" : "folder",
    libraryView: ["list", "gallery"].includes(p.get("library_view") || "")
      ? (p.get("library_view") as "list" | "gallery")
      : undefined,
  };
}
/**
 * What a reference (a search hit, a citation, a related-document link, a link
 * in an article) points at. It names the source only: the document or article
 * id and the position inside it. Which revision the person sees is decided by
 * the server when the source opens, never by this value.
 */
export type SourceTarget =
  | { kind: "wiki"; source: string; section?: string | null }
  | { kind: "document"; job: string; unit?: number | null };

/** The route that opens `target`, keeping the rest of `route` as it is. */
export function targetRoute(route: Route, target: SourceTarget): Route {
  return target.kind === "wiki"
    ? {
        ...route,
        mode: "wiki",
        source: target.source,
        section: target.section || null,
      }
    : {
        ...route,
        mode: "library",
        job: target.job || null,
        view: "preview",
        unit: target.unit || null,
      };
}

/** The same place as a real link, for new tab / copy link. Public format. */
export function targetUrl(target: SourceTarget) {
  const params = new URLSearchParams({
    mode: target.kind === "wiki" ? "wiki" : "library",
  });
  if (target.kind === "wiki") {
    params.set("source", target.source);
    if (target.section) params.set("section", target.section);
  } else {
    params.set("job", target.job);
    params.set("view", "preview");
    params.set("unit", String(target.unit || 1));
  }
  return withBase(`/?${params}`);
}

/** The source a route is showing, or null on a list / empty Wiki. */
export function targetOf(route: Route): SourceTarget | null {
  if (route.mode === "wiki")
    return route.source
      ? { kind: "wiki", source: route.source, section: route.section }
      : null;
  return route.job
    ? { kind: "document", job: route.job, unit: route.unit }
    : null;
}

/** True when both name the same article or document, wherever inside it. */
export function sameSource(a: SourceTarget | null, b: SourceTarget | null) {
  if (!a || !b || a.kind !== b.kind) return false;
  return a.kind === "wiki"
    ? a.source === (b as typeof a).source
    : a.job === (b as typeof a).job;
}

/**
 * The reference an in-app link (`/?mode=wiki&source=..`, `/?job=..`) stands
 * for, or null when the link is not an app address or names no source. Only
 * the same origin and the app's own path count; the query is read, not run.
 */
export function linkTarget(href: string, base: Location): SourceTarget | null {
  let url: URL;
  try {
    url = new URL(href, base.href);
  } catch {
    return null;
  }
  if (
    url.origin !== base.origin ||
    !["/", withBase("/")].includes(url.pathname)
  )
    return null;
  const source = url.searchParams.get("source"),
    job = url.searchParams.get("job");
  if (url.searchParams.get("mode") === "wiki")
    return source
      ? { kind: "wiki", source, section: url.searchParams.get("section") }
      : null;
  return job
    ? {
        kind: "document",
        job,
        unit: Number(url.searchParams.get("unit")) || null,
      }
    : null;
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
    if (next.libraryScope === "images")
      url.searchParams.set("library_scope", "images");
    if (next.libraryView)
      url.searchParams.set("library_view", next.libraryView);
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
