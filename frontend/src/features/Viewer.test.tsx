import type { ReactNode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Job } from "../lib/types";
import { Viewer } from "./Viewer";

vi.mock("../viewer/DocumentViewer", () => ({
  DocumentViewer: (props: {
    extensions: { menu: ReactNode; revision: string };
    onUnitChange: (n: number) => void;
    onLanguage: (language: string) => void;
  }) => (
    <div>
      <span>{props.extensions.revision}</span>
      {props.extensions.menu}
      <button onClick={() => props.onUnitChange(3)}>unit</button>
      <button onClick={() => props.onLanguage("en")}>viewer language</button>
    </div>
  ),
}));
vi.mock("./Translation", () => ({
  useTranslation: () => ({ revision: "rev-1" }),
}));
vi.mock("./Explanation", () => ({
  useExplanation: () => ({ panel: null }),
}));
vi.mock("./PrintPreview", () => ({
  PrintPreview: (props: { onClose: () => void; current: number }) => (
    <button onClick={props.onClose}>close print {props.current}</button>
  ),
}));

afterEach(cleanup);

function job(patch: Partial<Job> = {}): Job {
  return {
    id: "job",
    filename: "deck.pptx",
    original_filename: "元.pptx",
    folder_id: null,
    state: "success",
    created: 1,
    duration: 1,
    pages: 2,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: true,
    ...patch,
  };
}

test("opens print preview and restores focus to the document menu", () => {
  const onLanguage = vi.fn();
  render(
    <Viewer
      job={job()}
      view="preview"
      onView={() => {}}
      onBack={() => {}}
      message=""
      initialLanguage="original"
      onLanguage={onLanguage}
      initialUnit={2}
    />,
  );
  expect(screen.getByText("rev-1")).toBeInTheDocument();
  const menu = document.querySelector<HTMLDetailsElement>("#saveMenu")!;
  const summary = menu.querySelector("summary")!;
  menu.open = true;
  summary.focus();
  fireEvent.keyDown(menu, { key: "Escape" });
  expect(menu.open).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "プリントプレビュー" }));
  fireEvent.click(screen.getByRole("button", { name: "unit" }));
  expect(screen.getByRole("button", { name: "close print 3" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "close print 3" }));
  expect(summary).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "viewer language" }));
  expect(onLanguage).toHaveBeenCalledWith("en");
  expect(screen.getByRole("link", { name: "原文ドキュメントをダウンロード" })).toHaveAttribute(
    "href",
    expect.stringContaining("original.pptx"),
  );
});

test("disables printing until a failed document has a preview", () => {
  render(
    <Viewer
      job={job({
        filename: "scan.pdf",
        original_filename: null,
        preview: null,
        slide_layout: false,
        state: "failed",
      })}
      view="preview"
      onView={() => {}}
      onBack={() => {}}
      message=""
      initialLanguage="ja"
      onLanguage={() => {}}
      initialUnit={null}
    />,
  );
  expect(screen.getByRole("button", { name: "プリントプレビュー" })).toBeDisabled();
});
