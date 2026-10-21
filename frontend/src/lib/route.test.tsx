import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { readRoute, useRoute } from "./route";

afterEach(() => {
  history.replaceState({}, "", "/");
});

function Harness() {
  const { route, navigate } = useRoute();
  return (
    <div>
      <span>{`${route.mode}:${route.job}:${route.view}:${route.unit}:${route.folder}:${route.source}:${route.section}`}</span>
      <button
        onClick={() =>
          navigate({
            mode: "library",
            source: "src",
            section: "sec",
            unit: 2,
            folder: "fold",
            job: "job",
            view: "tables",
          })
        }
      >
        push
      </button>
      <button
        onClick={() =>
          navigate(
            {
              mode: "wiki",
              source: null,
              section: null,
              unit: null,
              folder: null,
              job: null,
              view: "preview",
            },
            false,
          )
        }
      >
        replace
      </button>
      <button
        onClick={() =>
          navigate({
            mode: "library",
            source: null,
            section: null,
            unit: null,
            folder: null,
            job: "job",
            view: "rag",
          })
        }
      >
        same
      </button>
    </div>
  );
}

test("reads library, wiki, and invalid query values", () => {
  history.replaceState({}, "", "/");
  expect(readRoute().mode).toBe("wiki");
  history.replaceState({}, "", "/?mode=library&view=nope&unit=0");
  expect(readRoute()).toMatchObject({
    mode: "library",
    view: "preview",
    unit: null,
  });
  history.replaceState({}, "", "/?job=j&unit=10001&view=structure");
  expect(readRoute()).toMatchObject({
    mode: "library",
    job: "j",
    unit: 10000,
    view: "structure",
  });
  history.replaceState({}, "", "/?folder=f&unit=abc");
  expect(readRoute()).toMatchObject({ mode: "library", folder: "f", unit: null });
  history.replaceState({}, "", "/?mode=wiki&source=s&section=h&view=rag");
  expect(readRoute()).toMatchObject({
    mode: "wiki",
    source: "s",
    section: "h",
    view: "rag",
  });
  history.replaceState({}, "", "/?view=tables");
  expect(readRoute().mode).toBe("library");
  history.replaceState({}, "", "/?unit=3");
  expect(readRoute()).toMatchObject({ mode: "library", unit: 3 });
});

test("pushes, replaces, and follows browser history", () => {
  history.replaceState({}, "", "/?mode=library&job=job&view=rag");
  render(<Harness />);
  expect(screen.getByText("library:job:rag:null:null:null:null")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "same" }));
  fireEvent.click(screen.getByRole("button", { name: "push" }));
  expect(location.search).toContain("view=tables");
  expect(location.search).toContain("unit=2");
  fireEvent.click(screen.getByRole("button", { name: "replace" }));
  expect(location.search).toBe("?mode=wiki");
  history.pushState({}, "", "/?mode=library&folder=back");
  fireEvent.popState(window);
  expect(screen.getByText("library:null:preview:null:back:null:null")).toBeInTheDocument();
});

import {
  linkTarget,
  sameSource,
  targetOf,
  targetRoute,
  targetUrl,
  type Route,
} from "./route";

const here: Route = {
  mode: "library",
  source: null,
  section: null,
  unit: null,
  folder: "f1",
  job: null,
  view: "tables",
};

test("a reference opens its source and keeps the rest of the route", () => {
  expect(targetRoute(here, { kind: "document", job: "j", unit: 4 })).toEqual({
    ...here,
    job: "j",
    view: "preview",
    unit: 4,
  });
  expect(
    targetRoute(here, { kind: "wiki", source: "a", section: "s" }),
  ).toEqual({ ...here, mode: "wiki", source: "a", section: "s" });
  expect(targetRoute(here, { kind: "wiki", source: "a" }).section).toBeNull();
});

test("the public link format of a reference is unchanged", () => {
  expect(targetUrl({ kind: "wiki", source: "a b", section: "x" })).toBe(
    "/?mode=wiki&source=a+b&section=x",
  );
  expect(targetUrl({ kind: "wiki", source: "a" })).toBe("/?mode=wiki&source=a");
  expect(targetUrl({ kind: "document", job: "j", unit: 2 })).toBe(
    "/?mode=library&job=j&view=preview&unit=2",
  );
});

test("what a route shows is the reference that would open it", () => {
  expect(targetOf(here)).toBeNull();
  expect(targetOf({ ...here, job: "j", unit: 3 })).toEqual({
    kind: "document",
    job: "j",
    unit: 3,
  });
  expect(targetOf({ ...here, mode: "wiki" })).toBeNull();
  expect(
    targetOf({ ...here, mode: "wiki", source: "a", section: "s" }),
  ).toEqual({ kind: "wiki", source: "a", section: "s" });
});

test("two references are the same source whatever the position inside it", () => {
  const doc = { kind: "document", job: "j" } as const;
  expect(sameSource(doc, { ...doc, unit: 9 })).toBe(true);
  expect(sameSource(doc, { kind: "document", job: "k" })).toBe(false);
  expect(
    sameSource({ kind: "wiki", source: "j" }, { kind: "document", job: "j" }),
  ).toBe(false);
  expect(sameSource(null, doc)).toBe(false);
});

test("only the app's own links stand for a reference", () => {
  const base = new URL("https://app.example/x/y");
  const at = (href: string) => linkTarget(href, base as unknown as Location);
  expect(at("/?mode=wiki&source=a&section=s")).toEqual({
    kind: "wiki",
    source: "a",
    section: "s",
  });
  expect(at("/?job=j&unit=4")).toEqual({
    kind: "document",
    job: "j",
    unit: 4,
  });
  expect(at("/?job=j&unit=zero")).toEqual({
    kind: "document",
    job: "j",
    unit: null,
  });
  expect(at("https://other.example/?job=j")).toBeNull();
  expect(at("/elsewhere?job=j")).toBeNull();
  expect(at("/?mode=wiki")).toBeNull();
  expect(at("/?mode=library")).toBeNull();
  expect(at("http://[bad")).toBeNull();
});
