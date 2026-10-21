import {
  sameSource,
  targetOf,
  type Route,
  type SourceTarget,
} from "../lib/route";

/**
 * Where "back" goes after a reference was followed (R05).
 *
 * The URL says where the person is (article, document, page, heading); it does
 * not say how they got there. That is kept here, in memory, for one hop:
 * the route they left and the source the reference opened. It is a hint for
 * the Back control only, never an address: a reload, a pasted link or a
 * different source shown later find no return point, and the browser's own
 * Back / Forward keep working from the history entries `navigate` pushed.
 */
export interface ReturnPoint {
  /** The kind of screen the reference was followed from. */
  origin: "search" | "wiki";
  /** The route the person left. */
  from: Route;
  /** The source the reference opened. The point only applies while it is shown. */
  target: SourceTarget;
}

export const returnLabel = (point: ReturnPoint) =>
  point.origin === "search" ? "検索結果へ戻る" : "元の記事へ戻る";

/** True when two routes name the same screen, source and position. */
export const sameRoute = (a: Route, b: Route) =>
  (Object.keys(a) as (keyof Route)[]).every((key) => a[key] === b[key]) &&
  (Object.keys(b) as (keyof Route)[]).every((key) => a[key] === b[key]);

/**
 * The person is back on the screen a search was opened from, without using the
 * Back control (the browser's Back button, or the same address): the search
 * itself can still be reopened with its results.
 */
export function searchToReopen(
  point: ReturnPoint | null,
  route: Route,
): ReturnPoint | null {
  return point?.origin === "search" && sameRoute(point.from, route)
    ? point
    : null;
}

/** The return point, if it still describes what `route` shows. */
export function activeReturn(
  point: ReturnPoint | null,
  route: Route,
): ReturnPoint | null {
  return point && sameSource(point.target, targetOf(route)) ? point : null;
}
