import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const { library, openUpload } = vi.hoisted(() => ({
  library: vi.fn(),
  openUpload: vi.fn(),
}));
vi.mock("./lib/api", () => ({ library }));
vi.mock("./features/Upload", () => ({
  useUpload: () => ({ open: openUpload, ui: null }),
}));
vi.mock("./features/Library", () => ({
  folderPath: () => "資料一覧",
  Library: (props: {
    hidden: boolean;
    message: string;
    navigation: ReactNode;
    onFolder: (id: string | null) => void;
    onJob: (job: { id: string }) => void;
    onUpload: () => void;
  }) => (
    <section aria-label="library" hidden={props.hidden}>
      {props.navigation}
      <p>{props.message}</p>
      <button id="fileSearch" onClick={() => props.onFolder("gone")}>
        missing folder
      </button>
      <button onClick={() => props.onJob({ id: "job" })}>open job</button>
      <button onClick={props.onUpload}>upload</button>
    </section>
  ),
}));
vi.mock("./features/Knowledge", () => ({
  ModeMenu: (props: {
    mode: string;
    onSearch: () => void;
    onMode: (mode: "library" | "wiki") => void;
  }) => (
    <div>
      <button onClick={props.onSearch}>search</button>
      <button onClick={() => props.onMode("wiki")}>wiki mode</button>
      <button onClick={() => props.onMode("library")}>library mode</button>
      <button onClick={() => props.onMode(props.mode as "library" | "wiki")}>
        same mode
      </button>
    </div>
  ),
  Wiki: (props: { hidden: boolean; navigation: ReactNode }) => (
    <section aria-label="wiki" hidden={props.hidden}>
      {props.navigation}
    </section>
  ),
  KnowledgeSearch: (props: { open: boolean; onClose: () => void }) =>
    props.open ? <button onClick={props.onClose}>close search</button> : null,
}));
vi.mock("./features/Viewer", () => ({
  Viewer: (props: {
    view: string;
    message: string;
    onView: (view: string) => void;
    onLanguage: (language: string) => void;
    onBack: () => void;
  }) => (
    <section aria-label="viewer">
      <span>{props.view}</span>
      <span>{props.message}</span>
      <button onClick={() => props.onView("tables")}>tables</button>
      <button onClick={() => props.onLanguage("ja")}>language</button>
      <button onClick={props.onBack}>back</button>
    </section>
  ),
}));

const job = {
  id: "job",
  filename: "a.pdf",
  folder_id: "folder",
  state: "success",
  created: 1,
  duration: 1,
  pages: 1,
  tables: 0,
  pictures: 0,
  chunks: 0,
  search_chunks: 0,
  rag_policy: null,
  error: null,
  preview: "preview.html",
  slide_layout: false,
};

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => {
    fn(0);
    return 1;
  });
  history.replaceState({}, "", "/?mode=library");
  library.mockResolvedValue({
    folders: [{ id: "folder", name: "F" }],
    jobs: [job],
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.title = "";
});

test("switches modes, opens search, and keeps a job view", async () => {
  renderApp();
  await waitFor(() => expect(document.title).toBe("Docling Desk · 資料一覧"));
  fireEvent.click(screen.getAllByRole("button", { name: "same mode" })[0]);
  fireEvent.click(screen.getAllByRole("button", { name: "search" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "close search" }));
  fireEvent.click(screen.getAllByRole("button", { name: "wiki mode" })[0]);
  expect(
    screen.getByRole("region", { name: "wiki", hidden: true }),
  ).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "library mode" })[0]);
  const opener = screen.getByRole("button", { name: "open job" });
  opener.focus();
  fireEvent.click(opener);
  expect(screen.getByRole("region", { name: "viewer" })).toHaveTextContent(
    "preview",
  );
  fireEvent.click(screen.getByRole("button", { name: "tables" }));
  expect(location.search).toContain("view=tables");
  fireEvent.click(screen.getByRole("button", { name: "language" }));
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(opener).toHaveFocus();
  expect(openUpload).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "upload" }));
  expect(openUpload).toHaveBeenCalled();
});

test("opens search with Ctrl+K and Cmd+K", () => {
  renderApp();
  expect(screen.queryByRole("button", { name: "close search" })).toBeNull();
  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
  fireEvent.click(screen.getByRole("button", { name: "close search" }));
  expect(screen.queryByRole("button", { name: "close search" })).toBeNull();
  fireEvent.keyDown(document, { key: "K", metaKey: true });
  expect(screen.getByRole("button", { name: "close search" })).toBeTruthy();
  fireEvent.keyDown(document, { key: "k" });
});

test("returns to the list when the folder or job disappears", async () => {
  history.replaceState({}, "", "/?mode=library&folder=missing");
  const missing = renderApp();
  await waitFor(() =>
    expect(
      screen.getByText("フォルダーが見つからないため、資料一覧へ戻りました。"),
    ).toBeInTheDocument(),
  );
  missing.unmount();
  history.replaceState({}, "", "/?mode=library&job=absent");
  library.mockReturnValue(new Promise(() => {}));
  const pending = renderApp();
  expect(await screen.findByText("読み込み中…")).toBeInTheDocument();
  pending.unmount();
  library.mockResolvedValue({ folders: [], jobs: [] });
  const absent = renderApp();
  expect(
    await screen.findByText(
      "資料が見つかりません。資料一覧へ戻って選び直してください。",
    ),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "資料一覧へ戻る" }));
  absent.unmount();
  library.mockRejectedValue(new Error("取得失敗"));
  renderApp();
  expect(await screen.findAllByText("取得失敗")).not.toHaveLength(0);
});
