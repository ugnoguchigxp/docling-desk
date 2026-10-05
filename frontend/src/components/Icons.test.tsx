import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { Icon, IconDefinitions } from "./Icons";

test("defines symbols and references one by name", () => {
  const view = render(
    <>
      <IconDefinitions />
      <Icon name="search" className="extra" />
    </>,
  );
  expect(view.container.querySelector("#i-search")).toBeTruthy();
  expect(view.container.querySelector("use")?.getAttribute("href")).toBe("#i-search");
  expect(view.container.querySelector(".icon.extra")).toBeTruthy();
});
