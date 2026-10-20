import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Viewer } from "./Viewer";
import type { Job } from "../lib/types";

afterEach(cleanup);

test("opens diagrams without extraction, translation or explanation requests", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const job: Job = {
    id: "diagram",
    filename: "図.drawio",
    diagram: true,
    folder_id: null,
    state: "success",
    created: 1,
    duration: null,
    pages: 2,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "diagram",
    slide_layout: false,
  };
  render(
    <Viewer
      job={job}
      view="preview"
      onView={() => {}}
      onBack={() => {}}
      message=""
      initialLanguage="original"
      onLanguage={() => {}}
      initialUnit={2}
    />,
  );
  const frame = screen.getByTitle("図のプレビュー");
  expect(frame).toHaveAttribute("src", "/view/diagram/diagram?unit=2");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts");
  expect(screen.getByRole("link", { name: "原本を保存" })).toHaveAttribute(
    "href",
    expect.stringContaining("original.drawio"),
  );
  expect(
    screen.queryByRole("button", { name: /翻訳|解説/ }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});
