import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job, Language } from "../lib/types";
import { PrintPreview } from "./PrintPreview";

let sharedDoc: Document;
let sharedWindow: Window & typeof globalThis;
let frameDoc: "ok" | "null" | "throw" = "ok";
let frameWindow: "ok" | "null" = "ok";
const print = vi.fn();
let documentDescriptor: PropertyDescriptor | undefined;
let windowDescriptor: PropertyDescriptor | undefined;

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job",
    filename: "report.pdf",
    original_filename: null,
    folder_id: null,
    state: "success",
    created: 1,
    duration: 1,
    pages: 3,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: false,
    ...overrides,
  };
}

const pageHtml =
  "<!DOCTYPE html><html><body><p>page</p></body></html>";
const pdfHtml = [
  '<div class="pdf-page" data-number="1" data-width="600" data-height="800"></div>',
  '<div class="pdf-page" data-number="2" data-width="600" data-height="800"></div>',
  '<div class="pdf-page" data-number="3" data-width="500" data-height="700"></div>',
].join("");

function response(body: string, status = 200) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html" },
  });
}

const gate: {
  resolve?: (value: Response) => void;
  reject?: (error: unknown) => void;
} = {};

function installFetch(failPath = "") {
  gate.resolve = undefined;
  gate.reject = undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), location.origin);
      if (failPath && url.pathname.includes(failPath))
        return response("no", 500);
      if (url.pathname.endsWith("/slides.json"))
        return Response.json({
          slides: [{ number: 1, width: 320, height: 180, preview: "a.html" }],
        });
      if (url.pathname.endsWith("/pdf")) {
        if (failPath === "hold-pdf")
          return new Promise<Response>((resolve, reject) => {
            gate.resolve = resolve;
            gate.reject = reject;
            init?.signal?.addEventListener("abort", () =>
              reject(init.signal?.reason),
            );
          });
        return response(pdfHtml);
      }
      if (failPath === "hold-page")
        return new Promise<Response>((resolve, reject) => {
          gate.resolve = resolve;
          gate.reject = reject;
          init?.signal?.addEventListener("abort", () =>
            reject(init.signal?.reason),
          );
        });
      return response(pageHtml);
    }),
  );
}

function openPreview(
  overrides: Partial<Job> = {},
  props: { language?: Language; current?: number; revision?: string } = {},
) {
  const onClose = vi.fn();
  const view = render(
    <PrintPreview
      job={job(overrides)}
      current={props.current ?? 1}
      language={props.language ?? "original"}
      revision={props.revision ?? "rev"}
      onClose={onClose}
    />,
  );
  return { ...view, onClose };
}

async function loadFrame() {
  fireEvent.load(screen.getByTitle("印刷する文書"));
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(
      "印刷の準備ができました。",
    ),
  );
}

beforeEach(() => {
  frameDoc = "ok";
  frameWindow = "ok";
  print.mockReset();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  documentDescriptor = Object.getOwnPropertyDescriptor(
    HTMLIFrameElement.prototype,
    "contentDocument",
  );
  windowDescriptor = Object.getOwnPropertyDescriptor(
    HTMLIFrameElement.prototype,
    "contentWindow",
  );
  const holder = document.createElement("iframe");
  document.body.append(holder);
  sharedDoc = holder.contentDocument!;
  sharedWindow = holder.contentWindow! as Window & typeof globalThis;
  sharedWindow.print = () => print();
  Object.defineProperty(sharedDoc, "fonts", {
    configurable: true,
    value: { ready: Promise.resolve() },
  });
  sharedWindow.HTMLImageElement.prototype.decode = () => Promise.resolve();
  Object.defineProperty(sharedWindow.HTMLImageElement.prototype, "naturalWidth", {
    configurable: true,
    get() {
      return 8;
    },
  });
  Object.defineProperty(HTMLIFrameElement.prototype, "contentDocument", {
    configurable: true,
    get() {
      if (frameDoc === "throw") throw new Error("cross-origin");
      if (frameDoc === "null") return null;
      return sharedDoc;
    },
  });
  Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
    configurable: true,
    get() {
      if (frameWindow === "null") return null;
      return sharedWindow;
    },
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  );
  installFetch();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  if (documentDescriptor)
    Object.defineProperty(
      HTMLIFrameElement.prototype,
      "contentDocument",
      documentDescriptor,
    );
  if (windowDescriptor)
    Object.defineProperty(
      HTMLIFrameElement.prototype,
      "contentWindow",
      windowDescriptor,
    );
  document.body.replaceChildren();
});

describe("PrintPreview", () => {
  it("prepares a pdf, changes paper and zoom, and prints", async () => {
    const { onClose } = openPreview({}, { language: "original", revision: "r/1" });
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "印刷用の文書を読み込んでいます…",
    );
    expect(screen.getByText("原文を印刷します。")).toBeInTheDocument();
    expect(
      screen.getByText("1ページ・1スライドを1枚に配置します。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "印刷の向き" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "印刷の余白" })).toBeDisabled();
    await loadFrame();
    expect(screen.getByRole("button", { name: "印刷・PDF保存" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "印刷・PDF保存" }));
    expect(print).toHaveBeenCalledOnce();
    fireEvent.change(screen.getByRole("combobox", { name: "印刷の用紙" }), {
      target: { value: "letter" },
    });
    await loadFrame();
    expect(screen.getByRole("combobox", { name: "印刷の向き" })).toBeEnabled();
    fireEvent.change(screen.getByRole("combobox", { name: "印刷の向き" }), {
      target: { value: "landscape" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "印刷の余白" }), {
      target: { value: "20" },
    });
    fireEvent.change(
      screen.getByRole("combobox", { name: "プリントプレビューの倍率" }),
      { target: { value: "0.5" } },
    );
    await loadFrame();
    fireEvent.change(screen.getByRole("combobox", { name: "印刷範囲" }), {
      target: { value: "range" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "印刷する範囲" }), {
      target: { value: "1、2" },
    });
    await loadFrame();
    fireEvent.change(screen.getByRole("combobox", { name: "印刷範囲" }), {
      target: { value: "current" },
    });
    await loadFrame();
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(onClose).toHaveBeenCalled();
    frameDoc = "null";
    fireEvent.change(
      screen.getByRole("combobox", { name: "プリントプレビューの倍率" }),
      { target: { value: "fit" } },
    );
  });

  it("describes Japanese and English output", async () => {
    const japanese = openPreview({}, { language: "ja" });
    await loadFrame();
    expect(screen.getByText("日本語表示を印刷します。")).toBeInTheDocument();
    japanese.unmount();
    openPreview({}, { language: "en" });
    await loadFrame();
    expect(screen.getByText("英語表示を印刷します。")).toBeInTheDocument();
  });

  it("prints a whole document without a page range", async () => {
    openPreview({ filename: "notes.docx", original_filename: "notes.docx" });
    expect(screen.queryByRole("combobox", { name: "印刷範囲" })).toBeNull();
    expect(
      screen.getByText(
        "本文は縦に続けて表示します。改ページはブラウザーの印刷画面で確認できます。",
      ),
    ).toBeInTheDocument();
    await loadFrame();
  });

  it("explains extracted slides and keeps the paper choice visible", async () => {
    openPreview(
      { filename: "deck.pptx", slide_layout: false },
      { language: "en" },
    );
    await loadFrame();
    expect(screen.getByText("原文を印刷します。")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("原本プレビューがない");
    expect(screen.getByRole("combobox", { name: "印刷の用紙" })).toHaveValue(
      "a4",
    );
  });

  it("prints laid-out slides and workbook sheets", async () => {
    const slides = openPreview({
      filename: "deck.pptx",
      slide_layout: true,
    });
    await loadFrame();
    expect(
      screen.getByRole("option", { name: "現在のページ・スライド（1）" }),
    ).toBeInTheDocument();
    slides.unmount();
    openPreview({ filename: "book.xlsx", pages: 2 });
    expect(
      screen.getByRole("option", { name: "現在のシート（1）" }),
    ).toBeInTheDocument();
    await loadFrame();
  });

  it("prints extracted text when the original preview is missing", async () => {
    openPreview({ preview: null, filename: "plain.txt" });
    await loadFrame();
    expect(screen.getByRole("note")).toBeInTheDocument();
  });

  it("reports source, range, frame, and print failures and retries", async () => {
    installFetch("/pdf");
    const failed = openPreview();
    fireEvent.load(screen.getByTitle("印刷する文書"));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
    installFetch();
    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    await loadFrame();
    failed.unmount();

    openPreview({}, { current: 9 });
    fireEvent.load(screen.getByTitle("印刷する文書"));
    fireEvent.change(screen.getByRole("combobox", { name: "印刷範囲" }), {
      target: { value: "current" },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "印刷するページを選んでください。",
    );
    fireEvent.change(screen.getByRole("combobox", { name: "印刷範囲" }), {
      target: { value: "range" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "印刷する範囲" }), {
      target: { value: "9-1" },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "範囲は1〜3で指定してください。",
    );
    cleanup();

    openPreview();
    const frame = screen.getByTitle("印刷する文書");
    const propsKey = Object.keys(frame).find((name) =>
      name.startsWith("__reactProps"),
    );
    const onError = propsKey
      ? (
          frame as unknown as Record<
            string,
            { onError?: (event: Event) => void }
          >
        )[propsKey]?.onError
      : undefined;
    act(() => {
      if (onError) onError(new Event("error"));
      else fireEvent.error(frame);
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "印刷用の画面を読み込めませんでした。再読み込みしてください。",
    );
    cleanup();

    frameDoc = "null";
    openPreview();
    fireEvent.load(screen.getByTitle("印刷する文書"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "印刷用の画面にアクセスできません。",
    );
    cleanup();

    frameDoc = "throw";
    openPreview();
    fireEvent.load(screen.getByTitle("印刷する文書"));
    expect(await screen.findByRole("alert")).toHaveTextContent("cross-origin");
    cleanup();

    frameDoc = "ok";
    openPreview();
    await loadFrame();
    frameWindow = "null";
    fireEvent.click(screen.getByRole("button", { name: "印刷・PDF保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "印刷用の画面を再読み込みしてください。",
    );
    cleanup();

    frameWindow = "ok";
    sharedWindow.print = () => {
      throw new Error("print blocked");
    };
    openPreview();
    await loadFrame();
    fireEvent.click(screen.getByRole("button", { name: "印刷・PDF保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("print blocked");
  });

  it("times out a frame that never loads and ignores a later timeout once loaded", async () => {
    vi.useFakeTimers();
    openPreview();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "印刷用の画面を読み込めませんでした。再読み込みしてください。",
    );
    cleanup();
    vi.useRealTimers();
    vi.useFakeTimers();
    openPreview();
    await act(async () => {
      await Promise.resolve();
      fireEvent.load(screen.getByTitle("印刷する文書"));
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45000);
    });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(
      "印刷の準備ができました。",
    );
  });

  it("drops results that resolve after the preview closes", async () => {
    installFetch("hold-pdf");
    const first = openPreview();
    first.unmount();
    await act(async () => {
      gate.resolve?.(response(pdfHtml));
    });
    installFetch("hold-page");
    const second = openPreview();
    fireEvent.load(screen.getByTitle("印刷する文書"));
    await waitFor(() => expect(gate.reject).toBeTypeOf("function"));
    second.unmount();
    await act(async () => {
      gate.reject?.(new Error("cancelled"));
    });
  });

  it("shows a page loading error from the preview frame", async () => {
    installFetch("/pages/");
    openPreview();
    fireEvent.load(screen.getByTitle("印刷する文書"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "印刷用の文書を読み込めませんでした（HTTP 500）",
    );
  });
});
