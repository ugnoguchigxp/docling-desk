import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { gridIcon, paths, TableIcon } from "./TableIcons";

test("draws every table icon as svg and as a grid span", () => {
  const view = render(
    <>
      {Object.keys(paths).map((name) => (
        <TableIcon key={name} name={name} className="marked" />
      ))}
    </>,
  );
  expect(view.container.querySelectorAll("path")).toHaveLength(Object.keys(paths).length);
  const span = gridIcon("filter");
  expect(span.className).toBe("table-grid-icon");
  expect(span.querySelector("path")?.getAttribute("d")).toBe(paths.filter);
  expect(span.getAttribute("aria-hidden")).toBe("true");
});
