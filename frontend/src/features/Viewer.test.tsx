import type { ReactNode } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
vi.mock("./DiagramEditor", () => ({
  DiagramEditor: (props: { job: Job; onChanged?: () => void }) => (
    <div data-testid="diagram-editor">
      {props.job.id}
      <button onClick={() => props.onChanged?.()}>changed</button>
    </div>
  ),
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

test("renders the Excalidraw editor for standalone diagrams, not the extraction viewer", () => {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const onBack = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <Viewer
        job={job({
          id: "scene",
          filename: "図.excalidraw",
          original_filename: null,
          preview: "excalidraw",
          slide_layout: false,
          pages: 0,
          excalidraw: true,
        })}
        view="preview"
        onView={() => {}}
        onBack={onBack}
        message="競合しました"
        initialLanguage="original"
        onLanguage={() => {}}
        initialUnit={null}
        navigation={<span>nav</span>}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByTestId("diagram-editor")).toHaveTextContent("scene");
  expect(screen.getByText("図.excalidraw")).toBeInTheDocument();
  expect(screen.getByText("nav")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("競合しました");
  expect(screen.queryByText("rev-1")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "資料一覧へ戻る" }));
  expect(onBack).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "changed" }));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["library"] });
});

test("keeps draw.io diagrams on the read-only viewer", () => {
  render(
    <Viewer
      job={job({ diagram: true, slide_layout: false, preview: null })}
      view="preview"
      onView={() => {}}
      onBack={() => {}}
      message=""
      initialLanguage="original"
      onLanguage={() => {}}
      initialUnit={null}
    />,
  );
  expect(screen.queryByTestId("diagram-editor")).not.toBeInTheDocument();
  expect(screen.getByLabelText("図のビューアー")).toBeInTheDocument();
});
