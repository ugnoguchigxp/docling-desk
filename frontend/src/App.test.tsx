import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { App } from "./App";
import type { SourceTarget } from "./lib/route";

const { library, openUpload, useUpload, searchRenders } = vi.hoisted(() => ({
  library: vi.fn(),
  openUpload: vi.fn(),
  useUpload: vi.fn(),
  searchRenders: vi.fn(),
}));
vi.mock("./lib/api", () => ({ library }));
vi.mock("./features/Upload", () => ({
  useUpload: (...args: unknown[]) => {
    useUpload(...args);
    return { open: openUpload, ui: null };
  },
}));
vi.mock("./features/Library", () => ({
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
  Wiki: (props: {
    hidden: boolean;
    navigation: ReactNode;
    onReference: (target: SourceTarget) => void;
  }) => (
    <section aria-label="wiki" hidden={props.hidden}>
      {props.navigation}
      <button
        onClick={() =>
          props.onReference({ kind: "document", job: "job", unit: 4 })
        }
      >
        related document
      </button>
    </section>
  ),
  KnowledgeSearch: (props: {
    open: boolean;
    resume: boolean;
    onClose: () => void;
    onReference: (target: SourceTarget) => void;
  }) => {
    searchRenders(props.open, props.resume);
    return props.open ? (
      <div>
        <button onClick={props.onClose}>close search</button>
        <button
          onClick={() => {
            props.onClose();
            props.onReference({ kind: "document", job: "job", unit: 3 });
          }}
        >
          open hit
        </button>
        <button
          onClick={() => {
            props.onClose();
            props.onReference({ kind: "document", job: "gone", unit: 1 });
          }}
        >
          open missing hit
        </button>
      </div>
    ) : null;
  },
}));
vi.mock("./features/Viewer", () => ({
  Viewer: (props: {
    view: string;
    message: string;
    backLabel?: string;
    navigation: ReactNode;
    onView: (view: string) => void;
    onLanguage: (language: string) => void;
    onBack: () => void;
  }) => (
    <section aria-label="viewer">
      {props.navigation}
      <span>{props.view}</span>
      <span>{props.message}</span>
      <button onClick={() => props.onView("tables")}>tables</button>
      <button onClick={() => props.onLanguage("ja")}>language</button>
      <button onClick={props.onBack}>{props.backLabel ?? "back"}</button>
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
  useUpload.mockClear();
  searchRenders.mockClear();
  cleanup();
  vi.unstubAllGlobals();
  document.title = "";
});

const visible = (name: string) =>
  screen
    .getAllByRole("region", { name, hidden: true })
    .find((node) => !node.hidden);

test("switches modes, opens search, and keeps a job view", async () => {
  renderApp();
  await waitFor(() => expect(document.title).toBe("Docling Desk · 資料一覧"));
  const entries = history.length;
  // The navigation is shown by the visible screen only, never twice.
  expect(
    screen.getAllByRole("navigation", { name: "表示モード" }),
  ).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "資料一覧" }));
  expect(history.length).toBe(entries);
  fireEvent.click(screen.getByRole("button", { name: "本文を検索" }));
  fireEvent.click(screen.getByRole("button", { name: "close search" }));
  fireEvent.click(screen.getByRole("button", { name: "Wiki" }));
  expect(
    screen.getByRole("region", { name: "wiki", hidden: true }),
  ).not.toHaveAttribute("hidden");
  expect(
    screen.getAllByRole("navigation", { name: "表示モード" }),
  ).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "資料一覧" }));
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

test("each mode remembers where it was when the person comes back", () => {
  history.replaceState({}, "", "/?mode=wiki&source=a&section=s");
  renderApp();
  fireEvent.click(screen.getByRole("button", { name: "資料一覧" }));
  expect(location.search).toBe("?mode=library");
  fireEvent.click(screen.getByRole("button", { name: "Wiki" }));
  expect(location.search).toBe("?mode=wiki&source=a&section=s");
  expect(visible("wiki")).toBeTruthy();
});

test("opens search with Ctrl+K and Cmd+K, once per key press", () => {
  const add = vi.spyOn(document, "addEventListener");
  const view = renderApp();
  expect(add.mock.calls.filter(([type]) => type === "keydown")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "close search" })).toBeNull();
  const opened = new KeyboardEvent("keydown", {
    key: "k",
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  act(() => void document.dispatchEvent(opened));
  expect(opened.defaultPrevented).toBe(true);
  expect(searchRenders).toHaveBeenLastCalledWith(true, false);
  fireEvent.click(screen.getByRole("button", { name: "close search" }));
  expect(screen.queryByRole("button", { name: "close search" })).toBeNull();
  fireEvent.keyDown(document, { key: "K", metaKey: true });
  expect(screen.getByRole("button", { name: "close search" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "close search" }));
  // Not ours: another key, a key already handled, a key being held down.
  fireEvent.keyDown(document, { key: "k" });
  const handled = new KeyboardEvent("keydown", {
    key: "k",
    ctrlKey: true,
    cancelable: true,
  });
  handled.preventDefault();
  document.dispatchEvent(handled);
  fireEvent.keyDown(document, { key: "k", ctrlKey: true, repeat: true });
  expect(screen.queryByRole("button", { name: "close search" })).toBeNull();
  view.unmount();
  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
  expect(screen.queryByRole("button", { name: "close search" })).toBeNull();
  add.mockRestore();
});

test("Back from a document acts once, however often it is pressed", async () => {
  renderApp();
  fireEvent.click(await screen.findByRole("button", { name: "open job" }));
  const entries = history.length;
  const back = screen.getByRole("button", { name: "back" });
  fireEvent.click(back);
  fireEvent.click(back);
  expect(location.search).toBe("?mode=library");
  // One step pushed for leaving the document; the second press did nothing.
  expect(history.length).toBe(entries + 1);
  expect(screen.queryByRole("region", { name: "viewer" })).toBeNull();
});

describe("after following a reference to its source", () => {
  async function followFromSearch() {
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "資料一覧" }));
    fireEvent.click(screen.getByRole("button", { name: "本文を検索" }));
    fireEvent.click(screen.getByRole("button", { name: "open hit" }));
  }

  test("Back returns to where the search was and reopens it with its results", async () => {
    history.replaceState({}, "", "/?mode=library&folder=folder");
    await followFromSearch();
    expect(location.search).toContain("job=job");
    expect(location.search).toContain("unit=3");
    expect(location.search).toContain("folder=folder");
    searchRenders.mockClear();
    const entries = history.length;
    const back = screen.getByRole("button", { name: "検索結果へ戻る" });
    fireEvent.click(back);
    fireEvent.click(back);
    expect(location.search).toBe("?mode=library&folder=folder");
    expect(history.length).toBe(entries + 1);
    // The dialog is open again and asked to keep its last results.
    expect(searchRenders).toHaveBeenCalledWith(true, true);
    expect(screen.getByRole("button", { name: "close search" })).toBeTruthy();
    // The hint is spent: another document opened from the list goes back to it.
    fireEvent.click(screen.getByRole("button", { name: "close search" }));
    expect(screen.queryByRole("button", { name: "検索結果へ戻る" })).toBeNull();
  });

  test("the browser Back button also leaves the search one click away", async () => {
    history.replaceState({}, "", "/?mode=library");
    await followFromSearch();
    act(() => {
      history.replaceState({}, "", "/?mode=library");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.queryByRole("region", { name: "viewer" })).toBeNull();
    // The source is no longer shown, so nothing offers to leave it.
    expect(screen.queryByRole("button", { name: "検索結果へ戻る" })).toBeNull();
    searchRenders.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "検索結果を開く" }));
    expect(searchRenders).toHaveBeenCalledWith(true, true);
    expect(location.search).toBe("?mode=library");
    fireEvent.click(screen.getByRole("button", { name: "close search" }));
    expect(screen.queryByRole("button", { name: "検索結果を開く" })).toBeNull();
  });

  test("a document opened some other way still goes back to the list", async () => {
    history.replaceState({}, "", "/?mode=library");
    await followFromSearch();
    fireEvent.click(screen.getByRole("button", { name: "検索結果へ戻る" }));
    fireEvent.click(screen.getByRole("button", { name: "close search" }));
    fireEvent.click(screen.getByRole("button", { name: "open job" }));
    expect(screen.queryByRole("button", { name: "検索結果へ戻る" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "back" }));
    expect(location.search).toBe("?mode=library");
    expect(searchRenders).not.toHaveBeenLastCalledWith(true, true);
  });

  test("a source that is gone is reported with the way back, never replaced", async () => {
    history.replaceState({}, "", "/?mode=library");
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "本文を検索" }));
    fireEvent.click(screen.getByRole("button", { name: "open missing hit" }));
    expect(
      await screen.findByText(
        "資料が見つかりません。資料一覧へ戻って選び直してください。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/別の資料に置き換えて表示することはしません/),
    ).toBeInTheDocument();
    expect(location.search).toContain("job=gone");
    expect(screen.queryByRole("region", { name: "viewer" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "検索結果へ戻る" }));
    expect(location.search).toBe("?mode=library");
  });

  test("from an article, Back returns to the same article and heading", async () => {
    history.replaceState({}, "", "/?mode=wiki&source=a&section=s");
    renderApp();
    fireEvent.click(
      await screen.findByRole("button", { name: "related document" }),
    );
    expect(location.search).toContain("job=job");
    fireEvent.click(screen.getByRole("button", { name: "元の記事へ戻る" }));
    expect(location.search).toBe("?mode=wiki&source=a&section=s");
    expect(visible("wiki")).toBeTruthy();
    // On the article the same hint is offered by the shared navigation until
    // another source is shown.
    expect(screen.queryByRole("button", { name: "元の記事へ戻る" })).toBeNull();
  });
});

test("says so when the source position is beyond the document", async () => {
  history.replaceState({}, "", "/?mode=library&job=job&view=preview&unit=9");
  library.mockResolvedValue({
    folders: [{ id: "folder", name: "F" }],
    jobs: [{ ...job, pages: 3 }],
  });
  renderApp();
  expect(
    await screen.findByText(/指定された位置（9）は資料の範囲外です/),
  ).toBeInTheDocument();
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

test("hands the upload the folder and its path, following the open job", async () => {
  library.mockResolvedValue({
    folders: [
      { id: "parent", name: "親", parent_id: null },
      { id: "folder", name: "F", parent_id: "parent" },
    ],
    jobs: [job],
  });
  history.replaceState({}, "", "/?mode=library&folder=parent");
  const view = renderApp();
  await waitFor(() =>
    expect(useUpload).toHaveBeenLastCalledWith(
      "parent",
      "資料一覧 / 親",
      expect.any(Function),
      true,
    ),
  );
  view.unmount();
  // A job open from another place uploads next to that job.
  history.replaceState({}, "", "/?mode=library&folder=parent&job=job");
  renderApp();
  await waitFor(() =>
    expect(useUpload).toHaveBeenLastCalledWith(
      "folder",
      "資料一覧 / 親 / F",
      expect.any(Function),
      true,
    ),
  );
});
