import { expect, it } from "vitest";
import type { Route } from "../lib/route";
import {
  activeReturn,
  returnLabel,
  sameRoute,
  searchToReopen,
  type ReturnPoint,
} from "./return-point";

const list: Route = {
  mode: "library",
  source: null,
  section: null,
  unit: null,
  folder: "f",
  job: null,
  view: "preview",
};
const point: ReturnPoint = {
  origin: "search",
  from: list,
  target: { kind: "document", job: "j", unit: 3 },
};

it("applies only while the source it opened is shown", () => {
  expect(activeReturn(point, { ...list, job: "j", unit: 7 })).toBe(point);
  expect(activeReturn(point, { ...list, job: "other" })).toBeNull();
  expect(activeReturn(point, list)).toBeNull();
  expect(activeReturn(null, { ...list, job: "j" })).toBeNull();
});

it("names where Back goes", () => {
  expect(returnLabel(point)).toBe("検索結果へ戻る");
  expect(returnLabel({ ...point, origin: "wiki" })).toBe("元の記事へ戻る");
});

it("offers the search again only on the screen it was opened from", () => {
  expect(searchToReopen(point, { ...list })).toBe(point);
  expect(searchToReopen(point, { ...list, folder: "g" })).toBeNull();
  expect(searchToReopen({ ...point, origin: "wiki" }, list)).toBeNull();
  expect(sameRoute(list, { ...list })).toBe(true);
  expect(sameRoute(list, { ...list, libraryView: "gallery" })).toBe(false);
});
