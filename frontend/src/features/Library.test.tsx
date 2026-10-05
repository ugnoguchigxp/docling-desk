import { useState, type ReactNode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Folder, Job, Library as Snapshot } from "../lib/types";
import { chain, folderPath, Library } from "./Library";

const dialogMethods = ["showModal", "close"] as const;
const originalDialogMethods = dialogMethods.map((name) =>
  Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name),
);

function installDialog() {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value() {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value() {
        const opened = this.hasAttribute("open");
        this.removeAttribute("open");
        if (opened) this.dispatchEvent(new Event("close"));
      },
    },
  });
}

function restoreDialog() {
  dialogMethods.forEach((name, index) => {
    const original = originalDialogMethods[index];
    if (original)
      Object.defineProperty(HTMLDialogElement.prototype, name, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  });
}

const requests: { url: string; body: unknown }[] = [];

function json(body: unknown = {}, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(
  impl?: (url: string, init?: RequestInit) => Response | Promise<Response>,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (typeof init?.body === "string")
        requests.push({ url, body: JSON.parse(init.body) as unknown });
      return impl ? impl(url, init) : json();
    }),
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function transfer(types: string[] = ["text/plain"]) {
  return {
    types: [...types],
    dropEffect: "none",
    effectAllowed: "all",
    files: [] as File[],
    items: [] as DataTransferItem[],
    setData() {},
    getData() {
      return "";
    },
  };
}

function job(
  partial: Pick<Job, "id" | "filename" | "folder_id" | "state" | "created"> &
    Partial<Job>,
): Job {
  return {
    original_filename: null,
    duration: null,
    pages: 1,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: null,
    slide_layout: false,
    ...partial,
  };
}

const folders: Folder[] = [
  { id: "a", name: "アルファ", parent_id: null, created: 300 },
  { id: "z", name: "オメガ", parent_id: null, created: 100 },
  { id: "b", name: "ベータ", parent_id: "a", created: 200 },
  { id: "d", name: "デルタ", parent_id: "a", created: 250 },
  { id: "c", name: "ガンマ", parent_id: "b", created: 100 },
  { id: "loop", name: "自己", parent_id: "loop", created: 50 },
  { id: "inside", name: "内部", parent_id: "loop", created: 40 },
];

const jobs: Job[] = [
  job({
    id: "pdf1",
    filename: "scan.pdf",
    folder_id: null,
    state: "success",
    created: 500,
    pages: 4,
    translations: {
      en: { saved: 2, active: 1, failed: 0 },
      ja: { saved: 1, active: 0, failed: 2 },
    },
  }),
  job({
    id: "docx1",
    filename: "converted.bin",
    original_filename: "report.docx",
    folder_id: null,
    state: "success",
    created: 450,
    pages: 0,
  }),
  job({
    id: "docx2",
    filename: "draft.docx",
    folder_id: null,
    state: "queued",
    created: 440,
    pages: 2,
  }),
  job({
    id: "txt1",
    filename: "notes.txt",
    folder_id: null,
    state: "running",
    created: 430,
    pages: 0,
  }),
  job({
    id: "text1",
    filename: "plain.text",
    folder_id: null,
    state: "failed",
    created: 420,
    pages: 3,
  }),
  job({
    id: "md1",
    filename: "readme.md",
    folder_id: null,
    state: "partial",
    created: 410,
    pages: 1,
    translations: { en: { saved: 1, active: 0, failed: 0 } },
  }),
  job({
    id: "readme",
    filename: "README",
    folder_id: null,
    state: "success",
    created: 90,
    pages: 0,
  }),
  job({
    id: "pdf2",
    filename: "older.pdf",
    folder_id: null,
    state: "success",
    created: 50,
    pages: 0,
    translations: { ja: { saved: 0, active: 1, failed: 0 } },
  }),
  job({
    id: "file-a",
    filename: "alpha-tie.pdf",
    folder_id: null,
    state: "success",
    created: 80,
    pages: 1,
  }),
  job({
    id: "file-b",
    filename: "beta-tie.pdf",
    folder_id: null,
    state: "success",
    created: 80,
    pages: 1,
  }),
  job({
    id: "markdown1",
    filename: "guide.markdown",
    folder_id: "a",
    state: "success",
    created: 300,
    pages: 2,
  }),
  job({
    id: "xlsx1",
    filename: "book.xlsx",
    folder_id: "a",
    state: "success",
    created: 300,
    pages: 1,
  }),
  job({
    id: "pptx1",
    filename: "deck.pptx",
    folder_id: "b",
    state: "success",
    created: 200,
    pages: 8,
    translations: { ja: { saved: 1, active: 1, failed: 0 } },
  }),
];

const libraryData: Snapshot = { folders, jobs };
const rootCount =
  folders.filter((folder) => folder.parent_id === null).length +
  jobs.filter((item) => item.folder_id === null).length;

function order() {
  return [...document.querySelectorAll("#jobs .file-name")].map(
    (node) => node.textContent,
  );
}

function rowByName(name: string) {
  const handle = screen.getByRole("button", { name: `${name}を開く` });
  const row = handle.closest("tr");
  if (!row) throw new Error(`missing row for ${name}`);
  return row as HTMLTableRowElement;
}

function openItemMenu(name: string) {
  const summary = document.querySelector(`summary[aria-label="${name}の操作"]`);
  if (!(summary instanceof HTMLElement)) throw new Error(`missing menu for ${name}`);
  fireEvent.click(summary);
  const details = summary.closest("details");
  if (details) details.open = true;
  return within(rowByName(name));
}

function Explorer({
  hidden = false,
  navigation,
  onJob,
  onUpload,
  initialFolder = null,
}: {
  hidden?: boolean;
  navigation?: ReactNode;
  onJob: (job: Job) => void;
  onUpload: () => void;
  initialFolder?: string | null;
}) {
  const [folder, setFolder] = useState(initialFolder);
  const [message, setMessage] = useState("");
  return (
    <Library
      data={libraryData}
      folder={folder}
      onFolder={setFolder}
      onJob={onJob}
      onUpload={onUpload}
      message={message}
      setMessage={setMessage}
      hidden={hidden}
      navigation={navigation}
    />
  );
}

function setup(props?: {
  hidden?: boolean;
  navigation?: ReactNode;
  initialFolder?: string | null;
}) {
  const onJob = vi.fn();
  const onUpload = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <Explorer
        onJob={onJob}
        onUpload={onUpload}
        hidden={props?.hidden}
        navigation={props?.navigation}
        initialFolder={props?.initialFolder ?? null}
      />
    </QueryClientProvider>,
  );
  return {
    ...view,
    onJob,
    onUpload,
    client,
    user: userEvent.setup({ delay: null }),
  };
}

beforeEach(() => {
  requests.length = 0;
  installDialog();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  installFetch();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  restoreDialog();
});

it("builds folder chains and display paths", () => {
  expect(chain(folders, null)).toEqual([]);
  expect(chain(folders, "")).toEqual([]);
  expect(chain(folders, "missing")).toEqual([]);
  expect(chain(folders, "c").map((folder) => folder.id)).toEqual([
    "a",
    "b",
    "c",
  ]);
  expect(
    chain(
      [{ id: "m", name: "M", parent_id: "gone", created: 1 }],
      "m",
    ).map((folder) => folder.id),
  ).toEqual(["m"]);
  const loop = [
    { id: "e", name: "E", parent_id: "f", created: 1 },
    { id: "f", name: "F", parent_id: "e", created: 2 },
  ];
  expect(chain(loop, "e").map((folder) => folder.id)).toEqual(["f", "e"]);
  expect(folderPath(folders, null)).toBe("資料一覧");
  expect(folderPath(folders, "c")).toBe("資料一覧 / アルファ / ベータ / ガンマ");
  expect(folderPath(folders, "loop")).toBe("資料一覧 / 自己");
});

it("renders folders and files, then filters, sorts, and empties the list", async () => {
  const { user, onUpload, onJob } = setup({ navigation: <a href="#nav">移動</a> });
  expect(screen.getByRole("link", { name: "移動" })).toBeInTheDocument();
  expect(document.getElementById("library")).not.toHaveAttribute("hidden");
  expect(document.getElementById("message")).toHaveAttribute("hidden");
  expect(document.getElementById("fileCount")).toHaveTextContent(
    `${rootCount}件`,
  );
  expect(order().indexOf("オメガ")).toBeLessThan(order().indexOf("scan.pdf"));
  expect(order().indexOf("アルファ")).toBeLessThan(order().indexOf("オメガ"));
  expect(order().indexOf("alpha-tie.pdf")).toBeLessThan(
    order().indexOf("beta-tie.pdf"),
  );
  expect(rowByName("scan.pdf")).toHaveTextContent("抽出完了");
  expect(rowByName("scan.pdf")).toHaveTextContent(/英訳/);
  expect(rowByName("scan.pdf")).toHaveTextContent(/翻訳中/);
  expect(rowByName("scan.pdf")).toHaveTextContent(/再実行可/);
  expect(within(rowByName("scan.pdf")).getByText("PDF")).toBeInTheDocument();
  expect(rowByName("converted.bin")).toHaveTextContent("文書全体");
  expect(rowByName("draft.docx")).toHaveTextContent("待機中");
  expect(rowByName("notes.txt")).toHaveTextContent("抽出中");
  expect(rowByName("plain.text")).toHaveTextContent("失敗");
  expect(rowByName("readme.md")).toHaveTextContent("一部抽出");
  expect(rowByName("readme.md")).toHaveTextContent(/英訳/);
  expect(rowByName("older.pdf")).toHaveTextContent(/日本語訳/);
  expect(rowByName("アルファ")).toHaveTextContent("フォルダー");
  expect(rowByName("README")).toHaveTextContent("README");
  expect(document.getElementById("libraryEmpty")).toHaveAttribute("hidden");

  const search = screen.getByRole("searchbox", { name: "このフォルダー内を検索" });
  await user.type(search, "  SCAN ");
  expect(order()).toEqual(["scan.pdf"]);
  expect(document.getElementById("fileCount")).toHaveTextContent(
    `1件 / ${rootCount}件`,
  );
  await user.clear(search);
  await user.type(search, "存在しない資料");
  expect(screen.getByText("一致する項目がありません。")).toBeInTheDocument();
  expect(document.getElementById("emptyAdd")).toHaveAttribute("hidden");
  expect(document.getElementById("fileCount")).toHaveTextContent(
    `0件 / ${rootCount}件`,
  );
  await user.clear(search);

  const type = screen.getByRole("combobox", { name: "ファイルの種類" });
  await user.selectOptions(type, "pdf");
  expect(order()).toContain("scan.pdf");
  expect(order()).toContain("アルファ");
  expect(order()).not.toContain("notes.txt");
  await user.selectOptions(type, "docx");
  expect(order()).toEqual(
    expect.arrayContaining(["converted.bin", "draft.docx", "アルファ"]),
  );
  expect(order()).not.toContain("readme.md");
  await user.selectOptions(type, "md");
  expect(order()).toContain("readme.md");
  expect(order()).not.toContain("plain.text");
  await user.selectOptions(type, "txt");
  expect(order()).toEqual(expect.arrayContaining(["notes.txt", "plain.text"]));
  expect(order()).not.toContain("scan.pdf");
  await user.selectOptions(type, "xlsx");
  expect(order()).not.toContain("book.xlsx");
  await user.selectOptions(type, "pptx");
  expect(order()).not.toContain("deck.pptx");
  await user.selectOptions(type, "");

  const sort = screen.getByRole("combobox", { name: "並べ替え" });
  await user.selectOptions(sort, "oldest");
  expect(order().indexOf("オメガ")).toBeLessThan(order().indexOf("アルファ"));
  expect(order().indexOf("older.pdf")).toBeLessThan(order().indexOf("scan.pdf"));
  await user.selectOptions(sort, "name");
  expect(order().indexOf("アルファ")).toBeLessThan(order().indexOf("オメガ"));
  expect(order().indexOf("alpha-tie.pdf")).toBeLessThan(
    order().indexOf("beta-tie.pdf"),
  );
  await user.selectOptions(sort, "recent");

  await user.type(search, "アルファ");
  fireEvent.click(rowByName("アルファ"));
  expect(search).toHaveValue("");
  expect(screen.getByRole("button", { name: "ベータを開く" })).toBeInTheDocument();
  expect(onJob).not.toHaveBeenCalled();

  fireEvent.click(rowByName("ベータ"));
  expect(screen.getByRole("button", { name: "ガンマを開く" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "deck.pptxを開く" })).toBeInTheDocument();
  fireEvent.click(rowByName("ガンマ"));
  expect(screen.getByText("このフォルダーは空です。")).toBeInTheDocument();
  expect(document.getElementById("emptyAdd")).not.toHaveAttribute("hidden");
  expect(document.getElementById("selectAll")).toBeDisabled();
  await user.click(document.getElementById("emptyAdd")!);
  await user.click(screen.getAllByRole("button", { name: "資料を追加" })[0]);
  expect(onUpload).toHaveBeenCalledTimes(2);
  await user.click(
    within(document.getElementById("breadcrumbs")!).getByRole("button", {
      name: "資料一覧",
    }),
  );
  expect(screen.getByRole("button", { name: "scan.pdfを開く" })).toBeInTheDocument();
  expect(
    screen.getByRole("checkbox", { name: "scan.pdfを選択" }),
  ).not.toBeChecked();
});

it("shows a hidden library and an existing status message", () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Library
        data={libraryData}
        folder={null}
        onFolder={vi.fn()}
        onJob={vi.fn()}
        onUpload={vi.fn()}
        message="保存しました"
        setMessage={vi.fn()}
        hidden
        navigation={<nav>移動</nav>}
      />
    </QueryClientProvider>,
  );
  expect(document.getElementById("library")).toHaveAttribute("hidden");
  expect(screen.getByText("保存しました")).toBeInTheDocument();
  expect(screen.getByText("移動")).toBeInTheDocument();
});

it("selects rows and downloads a single original file", async () => {
  const { user, onJob } = setup();
  const selectAll = screen.getByRole("checkbox", {
    name: "表示中の項目をすべて選択",
  });
  expect(selectAll).toBeEnabled();
  await user.click(screen.getByRole("checkbox", { name: "scan.pdfを選択" }));
  expect(onJob).not.toHaveBeenCalled();
  expect(rowByName("scan.pdf")).toHaveClass("selected");
  expect(selectAll).toBePartiallyChecked();
  expect(document.getElementById("selectionCount")).toHaveTextContent("1件選択");
  expect(document.getElementById("selectionRename")).toBeEnabled();
  expect(document.getElementById("selectionDownload")).toBeEnabled();

  await user.click(screen.getByRole("checkbox", { name: "アルファを選択" }));
  expect(document.getElementById("selectionCount")).toHaveTextContent("2件選択");
  expect(document.getElementById("selectionRename")).toBeDisabled();
  expect(document.getElementById("selectionDownload")).toBeDisabled();
  const folderClicks: string[] = [];
  const createElement = document.createElement.bind(document);
  const spy = vi.spyOn(document, "createElement").mockImplementation(((
    tag: string,
  ) => {
    const element = createElement(tag);
    if (tag === "a")
      element.click = () => {
        folderClicks.push((element as HTMLAnchorElement).href);
      };
    return element;
  }) as typeof document.createElement);
  fireEvent.click(document.getElementById("selectionDownload")!);
  expect(folderClicks).toEqual([]);
  spy.mockRestore();

  await user.click(screen.getByRole("checkbox", { name: "scan.pdfを選択" }));
  await user.click(screen.getByRole("checkbox", { name: "アルファを選択" }));
  expect(document.getElementById("selectionToolbar")).toHaveAttribute("hidden");
  fireEvent.click(document.getElementById("selectionDownload")!);
  fireEvent.click(document.getElementById("selectionDelete")!);
  expect(requests).toHaveLength(0);

  await user.click(selectAll);
  expect(selectAll).toBeChecked();
  expect(selectAll).not.toBePartiallyChecked();
  expect(document.getElementById("selectionCount")).toHaveTextContent(
    `${rootCount}件選択`,
  );
  await user.click(document.getElementById("selectionClear")!);
  expect(selectAll).not.toBeChecked();
  expect(document.getElementById("selectionToolbar")).toHaveAttribute("hidden");

  const scan = rowByName("scan.pdf");
  fireEvent.click(scan, { ctrlKey: true });
  expect(scan).toHaveClass("selected");
  expect(onJob).not.toHaveBeenCalled();
  fireEvent.click(scan, { metaKey: true });
  expect(scan).not.toHaveClass("selected");
  fireEvent.click(scan, { shiftKey: true });
  expect(scan).toHaveClass("selected");
  fireEvent.click(scan);
  expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ id: "pdf1" }));

  const menuEvent = new MouseEvent("contextmenu", {
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    scan.dispatchEvent(menuEvent);
  });
  expect(menuEvent.defaultPrevented).toBe(true);
  expect(scan.querySelector("details")).toHaveProperty("open", true);
  act(() => {
    rowByName("notes.txt").dispatchEvent(
      new MouseEvent("contextmenu", { bubbles: true, cancelable: true }),
    );
  });
  expect(scan.querySelector("details")).toHaveProperty("open", false);
  expect(rowByName("notes.txt").querySelector("details")).toHaveProperty(
    "open",
    true,
  );

  await user.click(document.getElementById("selectionClear")!);
  await user.click(screen.getByRole("checkbox", { name: "scan.pdfを選択" }));
  const hrefs: string[] = [];
  const restore = vi.spyOn(document, "createElement").mockImplementation(((
    tag: string,
  ) => {
    const element = createElement(tag);
    if (tag === "a")
      element.click = () => {
        hrefs.push((element as HTMLAnchorElement).href);
      };
    return element;
  }) as typeof document.createElement);
  fireEvent.click(document.getElementById("selectionDownload")!);
  restore.mockRestore();
  expect(hrefs[0]).toContain("/files/pdf1/original.pdf?download=true");
  expect(
    within(rowByName("scan.pdf")).getByRole("link", { name: "原本を保存" }),
  ).toHaveAttribute("href", expect.stringContaining("original.pdf?download=true"));
  expect(
    within(rowByName("converted.bin")).getByRole("link", { name: "原本を保存" }),
  ).toHaveAttribute("href", expect.stringContaining("original.docx?download=true"));
  expect(within(rowByName("アルファ")).queryByRole("link")).toBeNull();

  await user.click(openItemMenu("notes.txt").getByRole("button", { name: "開く" }));
  expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ id: "txt1" }));
});

it("creates, renames, moves, copies, and deletes from the toolbar and menus", async () => {
  const { user, onJob } = setup();
  fireEvent.submit(document.getElementById("libraryForm")!);
  expect(requests).toHaveLength(0);

  await user.click(screen.getByRole("button", { name: "新しいフォルダー" }));
  const dialog = document.getElementById("libraryDialog") as HTMLDialogElement;
  expect(dialog).toHaveAttribute("open");
  expect(screen.getByRole("heading", { name: "新しいフォルダー" })).toBeInTheDocument();
  expect(document.getElementById("operationDescription")).toHaveTextContent(
    "作成先：資料一覧",
  );
  expect(document.getElementById("operationName")).toHaveValue("");
  expect(document.getElementById("operationName")).toBeRequired();
  expect(document.getElementById("destinationBrowser")).toHaveAttribute("hidden");
  const form = document.getElementById("libraryForm") as HTMLFormElement;
  expect(form.checkValidity()).toBe(false);
  const invalid = vi.fn();
  form.addEventListener("invalid", invalid, true);
  form.requestSubmit();
  expect(invalid).toHaveBeenCalled();
  expect(requests).toHaveLength(0);
  await user.type(document.getElementById("operationName") as HTMLInputElement, "新規");
  await user.click(screen.getByRole("button", { name: "作成" }));
  await waitFor(() =>
    expect(screen.getByText("フォルダーを作成しました。")).toBeInTheDocument(),
  );
  expect(requests[0]).toEqual({
    url: "/api/folders",
    body: { name: "新規", parent_id: null },
  });
  expect(dialog).not.toHaveAttribute("open");

  await user.click(openItemMenu("アルファ").getByRole("button", { name: "開く" }));
  await user.click(screen.getByRole("button", { name: "新しいフォルダー" }));
  expect(document.getElementById("operationDescription")).toHaveTextContent(
    "作成先：資料一覧 / アルファ",
  );
  await user.type(document.getElementById("operationName") as HTMLInputElement, "子");
  await user.click(screen.getByRole("button", { name: "作成" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual({
    url: "/api/folders",
    body: { name: "子", parent_id: "a" },
  });
  await user.click(
    within(document.getElementById("breadcrumbs")!).getByRole("button", {
      name: "資料一覧",
    }),
  );

  await user.click(screen.getByRole("checkbox", { name: "converted.binを選択" }));
  await user.click(document.getElementById("selectionRename")!);
  expect(screen.getByRole("heading", { name: "名前を変更" })).toBeInTheDocument();
  expect(document.getElementById("operationDescription")).toHaveTextContent(
    "converted.bin",
  );
  const name = document.getElementById("operationName") as HTMLInputElement;
  expect(name).toHaveValue("converted.bin");
  await user.clear(name);
  await user.type(name, "renamed.bin");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() =>
    expect(screen.getByText("名前を変更しました。")).toBeInTheDocument(),
  );
  expect(requests.at(-1)).toMatchObject({
    url: "/api/library/operations",
    body: {
      action: "rename",
      name: "renamed.bin",
      items: [{ kind: "file", id: "docx1" }],
    },
  });

  await user.click(screen.getByRole("checkbox", { name: "scan.pdfを選択" }));
  await user.click(document.getElementById("selectionMove")!);
  expect(screen.getByRole("heading", { name: "移動先を選択" })).toBeInTheDocument();
  expect(document.getElementById("operationNameLabel")).toHaveAttribute("hidden");
  expect(document.getElementById("operationName")).not.toBeRequired();
  expect(document.getElementById("destinationHint")).toHaveTextContent(
    "フォルダーを開いて保存先を選んでください。",
  );
  const destinations = within(document.getElementById("destinationFolders")!);
  await user.click(destinations.getByRole("button", { name: "オメガ" }));
  expect(document.getElementById("destinationHint")).toHaveTextContent(
    "このフォルダーに下位フォルダーはありません。",
  );
  await user.click(
    within(document.getElementById("destinationBreadcrumbs")!).getByRole(
      "button",
      { name: "資料一覧" },
    ),
  );
  await user.click(
    within(document.getElementById("destinationFolders")!).getByRole("button", {
      name: "アルファ",
    }),
  );
  await user.click(
    within(document.getElementById("destinationFolders")!).getByRole("button", {
      name: "ベータ",
    }),
  );
  await user.click(
    within(document.getElementById("destinationFolders")!).getByRole("button", {
      name: "ガンマ",
    }),
  );
  expect(document.getElementById("destinationHint")).toHaveTextContent(
    "このフォルダーに下位フォルダーはありません。",
  );
  await user.click(screen.getByRole("button", { name: "ここに移動" }));
  await waitFor(() => expect(screen.getByText("移動しました。")).toBeInTheDocument());
  expect(requests.at(-1)).toMatchObject({
    body: {
      action: "move",
      destination: "c",
      parent: null,
      items: [{ kind: "file", id: "pdf1" }],
    },
  });

  await user.click(screen.getByRole("checkbox", { name: "notes.txtを選択" }));
  await user.click(screen.getByRole("checkbox", { name: "readme.mdを選択" }));
  await user.click(document.getElementById("selectionMove")!);
  expect(document.getElementById("operationDescription")).toHaveTextContent(
    "2件の項目",
  );
  await user.click(screen.getByRole("button", { name: "キャンセル" }));
  expect(dialog).not.toHaveAttribute("open");

  await user.click(openItemMenu("plain.text").getByRole("button", { name: "コピー先" }));
  expect(screen.getByRole("heading", { name: "コピー先を選択" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "ここにコピー" }));
  await waitFor(() => expect(screen.getByText("コピーしました。")).toBeInTheDocument());
  expect(requests.at(-1)).toMatchObject({
    body: {
      action: "copy",
      destination: null,
      items: [{ kind: "file", id: "text1" }],
    },
  });

  await user.click(screen.getByRole("checkbox", { name: "READMEを選択" }));
  await user.click(document.getElementById("selectionDelete")!);
  await waitFor(() => expect(screen.getByText("削除しました。")).toBeInTheDocument());
  expect(requests.at(-1)).toMatchObject({
    body: { action: "delete", items: [{ kind: "file", id: "readme" }] },
  });
  expect(onJob).not.toHaveBeenCalled();

  await user.click(openItemMenu("draft.docx").getByRole("button", { name: "名前を変更" }));
  expect(document.getElementById("operationName")).toHaveValue("draft.docx");
  await user.click(screen.getByRole("button", { name: "閉じる" }));
  await user.click(openItemMenu("draft.docx").getByRole("button", { name: "削除" }));
  await waitFor(() =>
    expect(requests.filter((request) => request.body && (request.body as { action?: string }).action === "delete")).toHaveLength(2),
  );
});

it("keeps dialog and inline errors visible until the next successful close", async () => {
  installFetch(() => json({ detail: "同名です" }, 409));
  const { user } = setup();
  await user.click(screen.getByRole("button", { name: "新しいフォルダー" }));
  await user.type(document.getElementById("operationName") as HTMLInputElement, "新規");
  await user.click(screen.getByRole("button", { name: "作成" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("同名です");
  expect(document.getElementById("libraryDialog")).toHaveAttribute("open");
  expect(document.getElementById("submitOperation")).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "キャンセル" }));
  expect(document.getElementById("libraryDialog")).not.toHaveAttribute("open");

  installFetch(() => json({ detail: "削除できません" }, 500));
  await user.click(screen.getByRole("checkbox", { name: "scan.pdfを選択" }));
  await user.click(document.getElementById("selectionDelete")!);
  expect(await screen.findByText("削除できません")).toBeInTheDocument();
  expect(document.getElementById("libraryDialog")).not.toHaveAttribute("open");
});

it("moves items by dragging them onto folders and breadcrumbs", async () => {
  const { user } = setup();
  const omega = rowByName("オメガ");
  fireEvent.dragOver(omega, { dataTransfer: transfer() });
  expect(omega).not.toHaveClass("drop-target");

  const alpha = screen.getByRole("button", { name: "アルファを開く" });
  const moving = transfer();
  fireEvent.dragStart(alpha, { dataTransfer: moving });
  fireEvent.dragOver(omega, { dataTransfer: moving });
  expect(omega).toHaveClass("drop-target");
  expect(moving.dropEffect).toBe("move");
  fireEvent.dragLeave(omega);
  expect(omega).not.toHaveClass("drop-target");
  fireEvent.dragOver(omega, { dataTransfer: moving });
  fireEvent.drop(omega, { dataTransfer: transfer(["Files"]) });
  expect(omega).toHaveClass("drop-target");
  expect(requests).toHaveLength(0);
  fireEvent.dragEnd(alpha);
  expect(omega).not.toHaveClass("drop-target");
  fireEvent.drop(omega, { dataTransfer: transfer() });
  expect(requests).toHaveLength(0);

  fireEvent.dragStart(alpha, { dataTransfer: moving });
  fireEvent.drop(omega, { dataTransfer: moving });
  await waitFor(() => expect(screen.getByText("移動しました。")).toBeInTheDocument());
  expect(requests.at(-1)).toMatchObject({
    body: {
      action: "move",
      destination: "z",
      items: [{ kind: "folder", id: "a" }],
    },
  });

  await user.click(screen.getByRole("checkbox", { name: "scan.pdfを選択" }));
  await user.click(screen.getByRole("checkbox", { name: "notes.txtを選択" }));
  const scan = screen.getByRole("button", { name: "scan.pdfを開く" });
  const selected = transfer();
  fireEvent.dragStart(scan, { dataTransfer: selected });
  fireEvent.drop(rowByName("オメガ"), { dataTransfer: selected });
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests.at(-1)).toMatchObject({
    body: {
      action: "move",
      destination: "z",
      items: expect.arrayContaining([
        { kind: "file", id: "pdf1" },
        { kind: "file", id: "txt1" },
      ]),
    },
  });

  await user.click(screen.getByRole("checkbox", { name: "readme.mdを選択" }));
  const lone = transfer();
  fireEvent.dragStart(screen.getByRole("button", { name: "older.pdfを開く" }), {
    dataTransfer: lone,
  });
  fireEvent.drop(rowByName("アルファ"), { dataTransfer: lone });
  await waitFor(() => expect(requests).toHaveLength(3));
  expect(requests.at(-1)).toMatchObject({
    body: {
      action: "move",
      destination: "a",
      items: [{ kind: "file", id: "pdf2" }],
    },
  });

  fireEvent.click(rowByName("アルファ"));
  fireEvent.click(rowByName("ベータ"));
  const crumb = within(document.getElementById("breadcrumbs")!).getByRole(
    "button",
    { name: "資料一覧" },
  );
  const deck = transfer();
  fireEvent.dragStart(screen.getByRole("button", { name: "deck.pptxを開く" }), {
    dataTransfer: deck,
  });
  fireEvent.dragOver(crumb, { dataTransfer: deck });
  expect(crumb).toHaveClass("drop-target");
  fireEvent.drop(crumb, { dataTransfer: deck });
  await waitFor(() => expect(requests).toHaveLength(4));
  expect(requests.at(-1)).toMatchObject({
    body: {
      action: "move",
      destination: null,
      items: [{ kind: "file", id: "pptx1" }],
    },
  });
});

it("blocks overlapping work while an operation is still running", async () => {
  const gate = deferred<Response>();
  let nested = false;
  installFetch(() => {
    if (!nested) {
      nested = true;
      fireEvent.submit(document.getElementById("libraryForm")!);
    }
    return gate.promise;
  });
  const { user, onJob } = setup();
  const moving = transfer();
  fireEvent.dragStart(screen.getByRole("button", { name: "scan.pdfを開く" }), {
    dataTransfer: moving,
  });
  await user.click(screen.getByRole("button", { name: "新しいフォルダー" }));
  await user.type(document.getElementById("operationName") as HTMLInputElement, "新規");
  await user.click(screen.getByRole("button", { name: "作成" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(document.getElementById("submitOperation")).toBeDisabled();

  fireEvent.dragOver(rowByName("オメガ"), { dataTransfer: moving });
  expect(rowByName("オメガ")).not.toHaveClass("drop-target");
  fireEvent.drop(rowByName("オメガ"), { dataTransfer: moving });
  expect(requests).toHaveLength(1);

  const blocked = new Event("dragstart", { bubbles: true, cancelable: true });
  Object.defineProperty(blocked, "dataTransfer", { value: transfer() });
  act(() => {
    screen.getByRole("button", { name: "notes.txtを開く" }).dispatchEvent(blocked);
  });
  expect(blocked.defaultPrevented).toBe(true);
  fireEvent.click(rowByName("notes.txt"));
  fireEvent.click(rowByName("アルファ"));
  expect(onJob).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "scan.pdfを開く" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "新しいフォルダー" }));
  expect(requests).toHaveLength(1);
  await user.click(openItemMenu("plain.text").getByRole("button", { name: "削除" }));
  expect(requests).toHaveLength(1);

  const dialog = document.getElementById("libraryDialog") as HTMLDialogElement;
  act(() => {
    dialog.dispatchEvent(new Event("cancel", { cancelable: true }));
  });
  expect(screen.getByRole("heading", { name: "新しいフォルダー" })).toBeInTheDocument();
  act(() => {
    dialog.dispatchEvent(new Event("close"));
  });
  expect(screen.getByRole("heading", { name: "新しいフォルダー" })).toBeInTheDocument();

  gate.resolve(json());
  await waitFor(() =>
    expect(screen.getByText("フォルダーを作成しました。")).toBeInTheDocument(),
  );
  expect(dialog).not.toHaveAttribute("open");
});

it("disables destinations that contain a folder being moved", async () => {
  const { user } = setup({ initialFolder: "loop" });
  await user.click(screen.getByRole("checkbox", { name: "自己を選択" }));
  await user.click(document.getElementById("selectionMove")!);
  expect(screen.getByRole("button", { name: "ここに移動" })).toBeDisabled();
  const destinations = within(document.getElementById("destinationFolders")!);
  expect(destinations.getByRole("button", { name: "自己" })).toBeDisabled();
  expect(destinations.getByRole("button", { name: "内部" })).toBeDisabled();
  expect(document.getElementById("destinationHint")).toHaveTextContent(
    "フォルダーを開いて保存先を選んでください。",
  );
  await user.click(screen.getByRole("button", { name: "キャンセル" }));
  await user.click(screen.getByRole("checkbox", { name: "自己を選択" }));
  await user.click(screen.getByRole("checkbox", { name: "内部を選択" }));
  await user.click(document.getElementById("selectionCopy")!);
  expect(screen.getByRole("button", { name: "ここにコピー" })).toBeEnabled();
  expect(
    within(document.getElementById("destinationFolders")!).getByRole("button", {
      name: "内部",
    }),
  ).toBeDisabled();
  expect(
    within(document.getElementById("destinationFolders")!).getByRole("button", {
      name: "自己",
    }),
  ).toBeEnabled();
});
