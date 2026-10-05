import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useUpload } from "./Upload";

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

function json(body: unknown = {}, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function file(name: string, size = 8) {
  const value = new File(["hello"], name);
  if (size !== value.size)
    Object.defineProperty(value, "size", { configurable: true, value: size });
  return value;
}

const limit = 50 * 1024 * 1024;

type UploadCall = { name: string; folder: FormDataEntryValue | null; provider: FormDataEntryValue | null };

function recordUpload(init?: RequestInit) {
  const body = init?.body;
  if (!(body instanceof FormData)) return null;
  const uploaded = body.get("file");
  return {
    name: uploaded instanceof File ? uploaded.name : "",
    folder: body.get("folder_id"),
    provider: body.get("ocr_provider"),
  } satisfies UploadCall;
}

function dragData(init?: {
  types?: string[];
  files?: File[];
  items?: unknown[];
}) {
  return {
    types: init?.types ?? ["Files"],
    files: init?.files ?? [],
    items: init?.items ?? [],
    dropEffect: "none",
    effectAllowed: "all",
  };
}

function assignFiles(input: HTMLInputElement, files: File[]) {
  const list = {
    length: files.length,
    item: (index: number) => files[index] ?? null,
    *[Symbol.iterator]() {
      yield* files;
    },
  };
  files.forEach((item, index) => {
    Object.defineProperty(list, index, { value: item });
  });
  Object.defineProperty(input, "files", { configurable: true, value: list });
}

function submitUpload() {
  fireEvent.submit(document.getElementById("upload")!);
}

function azureOption() {
  const option = document.querySelector('#upload option[value="azure_read"]');
  if (!(option instanceof HTMLOptionElement)) throw new Error("missing azure option");
  return option;
}

function dragLeaveAt(clientX: number, clientY: number) {
  const event = new Event("dragleave", { bubbles: true, cancelable: false });
  Object.defineProperty(event, "dataTransfer", { value: dragData() });
  Object.defineProperty(event, "clientX", { value: clientX });
  Object.defineProperty(event, "clientY", { value: clientY });
  act(() => {
    window.dispatchEvent(event);
  });
}

function entry(name: string, isDirectory: boolean) {
  return { name, isDirectory, isFile: !isDirectory } as FileSystemEntry;
}

function renderUpload(initial?: {
  destination?: string | null;
  path?: string;
  enabled?: boolean;
}) {
  const onMessage = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const props = {
    destination: null as string | null,
    path: "資料一覧",
    enabled: true,
    ...initial,
  };
  function Harness(current: typeof props) {
    const upload = useUpload(
      current.destination,
      current.path,
      onMessage,
      current.enabled,
    );
    return (
      <>
        <button type="button" onClick={() => upload.open()}>
          アップロードを開く
        </button>
        {upload.ui}
      </>
    );
  }
  const view = render(
    <QueryClientProvider client={client}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
  return {
    ...view,
    onMessage,
    client,
    user: userEvent.setup({ delay: null }),
    rerenderProps(next: Partial<typeof props>) {
      Object.assign(props, next);
      view.rerender(
        <QueryClientProvider client={client}>
          <Harness {...props} />
        </QueryClientProvider>,
      );
    },
  };
}

function postMessage(data: unknown, source: MessageEventSource | null = window) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data, source }));
  });
}

beforeEach(() => {
  installDialog();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/ocr" ? json({ azure_available: true }) : json({}),
    ),
  );
});

afterEach(() => {
  document.querySelectorAll("iframe").forEach((frame) => frame.remove());
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  restoreDialog();
});

it("validates the dialog and uploads the chosen files", async () => {
  const uploads: UploadCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/ocr") return json({ azure_available: true });
      if (url === "/api/upload") {
        const recorded = recordUpload(init);
        if (recorded) uploads.push(recorded);
        return json({});
      }
      return json({});
    }),
  );
  const { user, onMessage } = renderUpload({
    destination: "folder-a",
    path: "資料一覧 / A",
  });
  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  const dialog = document.getElementById("uploadDialog") as HTMLDialogElement;
  expect(dialog).toHaveAttribute("open");
  expect(document.getElementById("uploadLocation")).toHaveTextContent(
    "保存先：資料一覧 / A",
  );
  expect(screen.getByText("画像内の文字をこの環境で読み取り、RAGへ含めます。")).toBeInTheDocument();
  const form = document.getElementById("upload") as HTMLFormElement;
  expect(form.checkValidity()).toBe(false);
  const invalid = vi.fn();
  form.addEventListener("invalid", invalid, true);
  form.requestSubmit();
  expect(invalid).toHaveBeenCalled();
  expect(uploads).toHaveLength(0);

  const input = form.elements.namedItem("file");
  if (!(input instanceof HTMLInputElement)) throw new Error("missing file field");
  fireEvent.submit(form);
  expect(uploads).toHaveLength(0);
  expect(onMessage).not.toHaveBeenCalled();

  const reset = vi.spyOn(HTMLFormElement.prototype, "reset");
  assignFiles(input, [file("notes.PDF"), file("exact.pdf", limit)]);
  submitUpload();
  await waitFor(() => expect(onMessage).toHaveBeenCalled());
  const result = String(onMessage.mock.calls.at(-1)?.[0]);
  expect(result).toContain("2件の資料を追加しました");
  expect(uploads.map((item) => item.name)).toEqual(["notes.PDF", "exact.pdf"]);
  expect(uploads[0]).toMatchObject({ folder: "folder-a", provider: "local" });
  expect(dialog).not.toHaveAttribute("open");
  expect(reset).toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  expect(dialog).toHaveAttribute("open");
  expect(document.getElementById("uploadLocation")).toHaveTextContent(
    "保存先：資料一覧 / A",
  );
});

it("keeps the dialog open when every file is rejected", async () => {
  const uploads: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/ocr") return json({ azure_available: false });
      if (url === "/api/upload") {
        const recorded = recordUpload(init);
        uploads.push(recorded?.name ?? "");
        if (recorded?.name === "bad.pdf") return json({ detail: "容量不足" }, 413);
        if (recorded?.name === "offline.pdf") throw "offline";
        return json({});
      }
      return json({});
    }),
  );
  const { user, onMessage } = renderUpload();
  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  const input = document.querySelector("#upload input[type=file]");
  if (!(input instanceof HTMLInputElement)) throw new Error("missing file field");
  assignFiles(input, [
    file("bad.pdf"),
    file("offline.pdf"),
    file("notes.exe"),
    file("picture.png"),
    file("huge.pdf", limit + 1),
  ]);
  submitUpload();
  await waitFor(() => expect(onMessage).toHaveBeenCalled());
  const result = String(onMessage.mock.calls.at(-1)?.[0]);
  expect(result).toContain("資料を追加できませんでした。");
  expect(result).toContain("bad.pdf：容量不足");
  expect(result).toContain("offline.pdf：offline");
  expect(result).toContain("notes.exe：");
  expect(result).toContain("picture.png：");
  expect(result).toContain("huge.pdf：上限50 MiBを超えています。");
  expect(uploads).toEqual(["bad.pdf", "offline.pdf"]);
  expect(document.getElementById("uploadDialog")).toHaveAttribute("open");
  expect(document.getElementById("uploadMessage")).toHaveTextContent(
    "資料を追加できませんでした。",
  );
  await user.click(screen.getByRole("button", { name: "閉じる" }));
  expect(document.getElementById("uploadDialog")).not.toHaveAttribute("open");
});

it("ignores a missing file field and a file input without a file list", async () => {
  const { user } = renderUpload();
  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  const form = document.getElementById("upload") as HTMLFormElement;
  const input = form.elements.namedItem("file");
  if (!(input instanceof HTMLInputElement)) throw new Error("missing file field");
  Object.defineProperty(input, "files", { configurable: true, get: () => null });
  fireEvent.submit(form);
  input.remove();
  fireEvent.submit(form);
  expect(vi.mocked(fetch).mock.calls.some(([url]) => url === "/api/upload")).toBe(false);
});

it("switches OCR providers and survives a failed capability query", async () => {
  let releaseOcr!: (value: Response) => void;
  const ocrGate = new Promise<Response>((resolve) => {
    releaseOcr = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      url === "/api/ocr" ? ocrGate : json({ azure_available: true }),
    ),
  );
  const { user, rerenderProps } = renderUpload({ path: "資料一覧 / 固定" });
  expect(azureOption()).toBeDisabled();
  releaseOcr(json({ azure_available: true }));
  await waitFor(() => expect(azureOption()).toBeEnabled());
  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  const provider = screen.getByRole("combobox", { name: "画像から文字を読み取る方法" });
  fireEvent.dragEnter(window, { dataTransfer: dragData() });
  expect(screen.getByText("ローカルOCR")).toBeInTheDocument();
  expect(document.getElementById("fileDropLocation")).toHaveTextContent(
    "保存先：資料一覧 / 固定",
  );
  await user.selectOptions(provider, "disabled");
  expect(screen.getByText("OCRなし")).toBeInTheDocument();
  expect(screen.getByText("画像内の文字はRAGへ含めません。")).toBeInTheDocument();
  await user.selectOptions(provider, "azure_read");
  expect(screen.getByText(/Azure OCR：対象PDFページ全体/)).toBeInTheDocument();
  expect(screen.getByText(/PDFは対象ページ全体/)).toBeInTheDocument();
  await user.selectOptions(provider, "local");
  expect(screen.getByText("ローカルOCR")).toBeInTheDocument();
  rerenderProps({ path: "資料一覧 / 変更後" });
  expect(document.getElementById("uploadLocation")).toHaveTextContent(
    "保存先：資料一覧 / 固定",
  );
  expect(document.getElementById("fileDropLocation")).toHaveTextContent(
    "保存先：資料一覧 / 固定",
  );

  cleanup();
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/ocr") throw new Error("ocr down");
      return json({});
    }),
  );
  const failed = renderUpload();
  await waitFor(() =>
    expect(failed.client.getQueryState(["ocr-capabilities"])?.status).toBe("error"),
  );
  expect(azureOption()).toBeDisabled();
  await failed.user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  const field = document.querySelector("#upload input[type=file]");
  if (!(field instanceof HTMLInputElement)) throw new Error("missing file field");
  const uploads: UploadCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/upload") {
        const recorded = recordUpload(init);
        if (recorded) uploads.push(recorded);
      }
      if (url === "/api/ocr") throw new Error("ocr down");
      return json({});
    }),
  );
  assignFiles(field, [file("kept.pdf")]);
  submitUpload();
  await waitFor(() => expect(uploads.map((item) => item.name)).toEqual(["kept.pdf"]));
});

it("locks the dialog until the in-flight upload finishes", async () => {
  const pending: Array<(value: Response) => void> = [];
  const uploads: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/ocr") return json({ azure_available: true });
      if (url === "/api/upload") {
        const recorded = recordUpload(init);
        if (recorded) uploads.push(recorded.name);
        return new Promise<Response>((resolve) => pending.push(resolve));
      }
      return json({});
    }),
  );
  const { user, onMessage } = renderUpload({ destination: null, path: "資料一覧" });
  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  const input = document.querySelector("#upload input[type=file]");
  if (!(input instanceof HTMLInputElement)) throw new Error("missing file field");
  assignFiles(input, [file("first.pdf"), file("second.pdf")]);
  submitUpload();
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(document.getElementById("uploadMessage")).toHaveTextContent(
    "アップロード中… 1 / 2件",
  );
  expect(screen.getByRole("button", { name: "抽出を開始" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "画像から文字を読み取る方法" })).toBeDisabled();

  const hovering = dragData();
  fireEvent.dragOver(window, { dataTransfer: hovering });
  expect(hovering.dropEffect).toBe("none");
  expect(screen.getByText("アップロード中です。完了までお待ちください")).toBeInTheDocument();
  fireEvent.drop(window, {
    dataTransfer: dragData({ files: [file("later.pdf")] }),
  });
  expect(onMessage).toHaveBeenCalledWith(
    "アップロード中です。終了してから、もう一度ドロップしてください。",
  );
  expect(uploads).toEqual(["first.pdf"]);
  const dialog = document.getElementById("uploadDialog") as HTMLDialogElement;
  fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
  act(() => {
    dialog.dispatchEvent(new Event("close"));
  });
  expect(dialog).toHaveAttribute("open");

  pending[0](json());
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(document.getElementById("uploadMessage")).toHaveTextContent(
    "アップロード中… 2 / 2件",
  );
  pending[1](json());
  await waitFor(() => expect(onMessage).toHaveBeenCalledWith(expect.stringContaining("2件の資料を追加しました")));
  expect(dialog).not.toHaveAttribute("open");
  expect(uploads).toEqual(["first.pdf", "second.pdf"]);
});

it("shows and hides the drop overlay from pointer, keyboard, and focus events", () => {
  renderUpload();
  const overlay = () => document.getElementById("fileDropOverlay")!;
  const files = () => dragData();
  fireEvent.dragEnter(window);
  fireEvent.dragEnter(window, { dataTransfer: dragData({ types: ["text/plain"] }) });
  fireEvent.dragOver(window, { dataTransfer: dragData({ types: ["text/plain"] }) });
  fireEvent.dragLeave(window, { dataTransfer: dragData({ types: ["text/plain"] }) });
  fireEvent.drop(window, { dataTransfer: dragData({ types: ["text/plain"] }) });
  expect(overlay().hidden).toBe(true);

  dragLeaveAt(10, 10);
  fireEvent.dragEnter(window, { dataTransfer: files() });
  fireEvent.dragEnter(window, { dataTransfer: files() });
  dragLeaveAt(10, 10);
  expect(overlay().hidden).toBe(false);
  fireEvent.dragEnter(window, { dataTransfer: files() });
  dragLeaveAt(0, 10);
  expect(overlay().hidden).toBe(true);

  fireEvent.dragEnter(window, { dataTransfer: files() });
  fireEvent.dragEnter(window, { dataTransfer: files() });
  dragLeaveAt(10, 0);
  expect(overlay().hidden).toBe(true);

  fireEvent.dragEnter(window, { dataTransfer: files() });
  fireEvent.dragEnter(window, { dataTransfer: files() });
  dragLeaveAt(window.innerWidth, 10);
  expect(overlay().hidden).toBe(true);

  fireEvent.dragEnter(window, { dataTransfer: files() });
  fireEvent.dragEnter(window, { dataTransfer: files() });
  dragLeaveAt(10, window.innerHeight);
  expect(overlay().hidden).toBe(true);

  fireEvent.dragEnter(window, { dataTransfer: files() });
  dragLeaveAt(10, 10);
  expect(overlay().hidden).toBe(true);

  const copying = files();
  fireEvent.dragOver(window, { dataTransfer: copying });
  expect(copying.dropEffect).toBe("copy");
  expect(overlay().hidden).toBe(false);
  expect(screen.getByText("ここにドロップして抽出を開始")).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Enter" });
  expect(overlay().hidden).toBe(false);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(overlay().hidden).toBe(true);

  fireEvent.dragEnter(window, { dataTransfer: files() });
  fireEvent.blur(window);
  expect(overlay().hidden).toBe(true);
  fireEvent.dragEnter(window, { dataTransfer: files() });
  fireEvent.dragEnd(window);
  expect(overlay().hidden).toBe(true);
});

it("accepts dropped files, directories, and messages from a child frame", async () => {
  const uploads: UploadCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/ocr") return json({ azure_available: true });
      if (url === "/api/upload") {
        const recorded = recordUpload(init);
        if (recorded) uploads.push(recorded);
        if (recorded?.name === "bad.pdf") return json({ detail: "拒否" }, 400);
        return json({});
      }
      return json({});
    }),
  );
  const { onMessage, user, rerenderProps } = renderUpload({
    destination: "folder-live",
    path: "資料一覧 / 現在",
  });
  fireEvent.drop(window, {
    dataTransfer: dragData({
      items: [
        {
          kind: "file",
          webkitGetAsEntry: () => entry("docs", true),
          getAsFile: () => file("docs"),
        },
        {
          kind: "file",
          webkitGetAsEntry: () => null,
          getAsFile: () => null,
        },
        { kind: "text", getAsFile: () => file("skip.pdf") },
        { kind: "file", getAsFile: () => file("plain.pdf") },
        {
          kind: "file",
          webkitGetAsEntry: () => entry("bad.pdf", false),
          getAsFile: () => file("bad.pdf"),
        },
      ],
    }),
  });
  await waitFor(() => expect(onMessage).toHaveBeenCalled());
  const dropped = String(onMessage.mock.calls.at(-1)?.[0]);
  expect(dropped).toContain("1件の資料を追加しました");
  expect(dropped).toContain("docs：フォルダーではなく、ファイルをドロップしてください。");
  expect(dropped).toContain("bad.pdf：拒否");
  expect(uploads.map((item) => item.name)).toEqual(["plain.pdf", "bad.pdf"]);
  expect(uploads[0]?.folder).toBe("folder-live");

  onMessage.mockClear();
  fireEvent.drop(window, {
    dataTransfer: dragData({
      items: [{ kind: "file", webkitGetAsEntry: () => null, getAsFile: () => null }],
    }),
  });
  expect(onMessage).not.toHaveBeenCalled();

  fireEvent.drop(window, { dataTransfer: dragData({ items: [], files: [file("fallback.pdf")] }) });
  await waitFor(() => expect(uploads.map((item) => item.name)).toContain("fallback.pdf"));

  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  const source = iframe.contentWindow;
  if (!source) throw new Error("missing frame");
  postMessage(null);
  postMessage(["docling-file-drop"]);
  postMessage("docling-file-drop");
  postMessage({ type: "other" }, source);
  postMessage({ type: "docling-file-drop", action: "enter", time: Date.now() });
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(true);

  postMessage({ type: "docling-file-drop", action: "enter", time: Date.now() }, source);
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(false);
  postMessage({ type: "docling-file-drop", action: "leave", time: 1 }, source);
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(false);
  postMessage({ type: "docling-file-drop", action: "leave", time: "now" }, source);
  postMessage({ type: "docling-file-drop", action: "hover", time: Date.now() }, source);
  postMessage(
    { type: "docling-file-drop", action: "leave", time: Date.now() + 1000 },
    source,
  );
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(true);

  postMessage(
    { type: "docling-file-drop", action: "drop", files: [file("frame.pdf"), "nope"], rejected: [] },
    source,
  );
  postMessage(
    { type: "docling-file-drop", action: "drop", files: [file("frame.pdf")], rejected: [1] },
    source,
  );
  postMessage(
    { type: "docling-file-drop", action: "drop", files: "frame.pdf", rejected: [] },
    source,
  );
  postMessage(
    { type: "docling-file-drop", action: "drop", files: [], rejected: "no" },
    source,
  );
  postMessage({ type: "docling-file-drop", action: "drop", files: [], rejected: [] }, source);
  expect(uploads.map((item) => item.name)).not.toContain("frame.pdf");

  await user.click(screen.getByRole("button", { name: "アップロードを開く" }));
  rerenderProps({ destination: "folder-next", path: "資料一覧 / 次" });
  expect(document.getElementById("uploadLocation")).toHaveTextContent("保存先：資料一覧 / 現在");
  postMessage(
    {
      type: "docling-file-drop",
      action: "drop",
      files: [file("while-open.pdf")],
      rejected: ["枠で拒否"],
    },
    source,
  );
  await waitFor(() => expect(uploads.map((item) => item.name)).toContain("while-open.pdf"));
  expect(uploads.at(-1)?.folder).toBe("folder-live");
  expect(String(onMessage.mock.calls.at(-1)?.[0])).toContain("枠で拒否");
  expect(document.getElementById("uploadDialog")).toHaveAttribute("open");

  postMessage({ type: "docling-file-drop", action: "enter", time: 1 }, source);
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(true);
  postMessage(
    { type: "docling-file-drop", action: "enter", time: Date.now() + 1000 },
    source,
  );
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(false);
  expect(document.getElementById("fileDropLocation")).toHaveTextContent("保存先：資料一覧 / 現在");

  await user.click(screen.getByRole("button", { name: "閉じる" }));
  rerenderProps({ destination: null, path: "資料一覧 / 閉じた後" });
  fireEvent.drop(window, { dataTransfer: dragData({ files: [file("after-close.pdf")] }) });
  await waitFor(() => expect(uploads.map((item) => item.name)).toContain("after-close.pdf"));
  expect(uploads.at(-1)?.folder).toBeNull();
  fireEvent.dragEnter(window, { dataTransfer: dragData() });
  expect(document.getElementById("fileDropLocation")).toHaveTextContent(
    "保存先：資料一覧 / 閉じた後",
  );
});

it("does not arm the window when uploading is disabled", async () => {
  const disabled = renderUpload({ enabled: false, path: "資料一覧" });
  fireEvent.dragEnter(window, { dataTransfer: dragData() });
  fireEvent.drop(window, { dataTransfer: dragData({ files: [file("nope.pdf")] }) });
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(true);
  expect(disabled.onMessage).not.toHaveBeenCalled();

  cleanup();
  const view = renderUpload({ enabled: true });
  fireEvent.dragEnter(window, { dataTransfer: dragData() });
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(false);
  view.rerenderProps({ enabled: false });
  expect(document.getElementById("fileDropOverlay")!.hidden).toBe(true);
  fireEvent.drop(window, { dataTransfer: dragData({ files: [file("nope.pdf")] }) });
  expect(view.onMessage).not.toHaveBeenCalled();
  view.rerenderProps({ enabled: true });
  fireEvent.drop(window, { dataTransfer: dragData({ files: [file("again.pdf")] }) });
  await waitFor(() =>
    expect(view.onMessage).toHaveBeenCalledWith(expect.stringContaining("1件の資料を追加しました")),
  );
});

it("enables dragging when the caller omits the enabled flag", async () => {
  const onMessage = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Harness() {
    const upload = useUpload("folder-default", "資料一覧", onMessage);
    return upload.ui;
  }
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(azureOption()).toBeEnabled());
  fireEvent.drop(window, { dataTransfer: dragData({ files: [file("default.pdf")] }) });
  await waitFor(() =>
    expect(onMessage).toHaveBeenCalledWith(expect.stringContaining("1件の資料を追加しました")),
  );
  const uploadCall = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => url === "/api/upload");
  expect(recordUpload(uploadCall?.[1])?.folder).toBe("folder-default");
});
