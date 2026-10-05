import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import {
  localViewerSource,
  sessionViewerSource,
  useViewerSource,
  ViewerSource,
} from "./data-source";

function Probe() {
  const source = useViewerSource();
  return <span>{source.url("/files/job/original.pdf")}</span>;
}

test("keeps local paths and remaps session paths", () => {
  expect(localViewerSource.url("/api/library")).toBe("/api/library");
  const source = sessionViewerSource("https://desk.example/session/");
  expect(source.url("/files/job/original.pdf")).toBe(
    "https://desk.example/session/file/original.pdf",
  );
  expect(source.url("/view/job/pages/1")).toBe(
    "https://desk.example/session/view/pages/1",
  );
  expect(source.url("/api/jobs/job/translations")).toBe(
    "https://desk.example/session/api/translations",
  );
  expect(() => source.url("/api/library")).toThrow("閲覧専用");
  render(
    <ViewerSource.Provider value={source}>
      <Probe />
    </ViewerSource.Provider>,
  );
  expect(screen.getByText("https://desk.example/session/file/original.pdf")).toBeInTheDocument();
  render(<Probe />);
  expect(screen.getByText("/files/job/original.pdf")).toBeInTheDocument();
});
