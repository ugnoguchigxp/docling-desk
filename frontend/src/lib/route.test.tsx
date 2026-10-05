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
