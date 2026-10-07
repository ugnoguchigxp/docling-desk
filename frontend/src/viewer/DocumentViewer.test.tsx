import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job, Language, View } from "../lib/types";
import { DocumentViewer } from "./DocumentViewer";

const tablesControl = vi.hoisted(() => ({
  mode: "ok" as "ok" | "throw",
  page: 2,
}));

vi.mock("../features/Tables", async () => {
  const React = await import("react");
  return {
    Tables({
      visible,
      onSource,
    }: {
      visible: boolean;
      onSource: (page: number) => void;
    }) {
      if (tablesControl.mode === "throw") throw new Error("tables failed");
      return React.createElement(
        "div",
        { id: "tables-mock" },
        React.createElement(
          "button",
          { type: "button", onClick: () => onSource(tablesControl.page) },
          "表の出典",
        ),
        visible ? "tables-visible" : "tables-hidden",
      );
    },
  };
});

let slidesMode: "ok" | "empty" | "error" = "ok";

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "job",
    filename: "report.pdf",
    original_filename: null,
    folder_id: null,
    state: "success",
    created: 1,
    duration: 1,
    pages: 4,
    tables: 1,
    pictures: 0,
    chunks: 1,
    search_chunks: 1,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: false,
    ...overrides,
  };
}

function original() {
  const node = document.getElementById("original");
  if (!(node instanceof HTMLIFrameElement)) throw new Error("missing frame");
  return node;
}

function Viewer({
  job: chosen = job(),
  view: initialView = "preview",
  onView = vi.fn(),
  onBack = vi.fn(),
  message = "",
  initialLanguage = "original",
  navigation,
  initialUnit,
  embedded = false,
  onUnitChange,
  onQuestion,
  extensions,
}: {
  job?: Job;
  view?: View;
  onView?: (view: View) => void;
  onBack?: () => void;
  message?: string;
  initialLanguage?: Language;
  navigation?: ReactNode;
  initialUnit?: number | null;
  embedded?: boolean;
  onUnitChange?: (unit: number | null) => void;
  onQuestion?: (text: string, unit: number) => void;
  extensions?: Parameters<typeof DocumentViewer>[0]["extensions"];
}) {
  const [view, setView] = useState(initialView);
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <DocumentViewer
        job={chosen}
        view={view}
        onView={(next) => {
          onView(next);
          setView(next);
        }}
        onBack={onBack}
        message={message}
        initialLanguage={initialLanguage}
        onLanguage={() => {}}
        navigation={navigation}
        initialUnit={initialUnit}
        embedded={embedded}
        onUnitChange={onUnitChange}
        onQuestion={onQuestion}
        extensions={extensions}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  slidesMode = "ok";
  tablesControl.mode = "ok";
  tablesControl.page = 2;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  );
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("slides.json")) {
        if (slidesMode === "error")
          return new Response(JSON.stringify({ detail: "bad slides" }), {
            status: 500,
            headers: { "content-type": "application/json" },
          });
        if (slidesMode === "empty")
          return Response.json({
            slides: [{ number: 1, width: 200, height: 100, preview: null }],
          });
        return Response.json({
          slides: [
            { number: 1, width: 320, height: 180, preview: "one.html" },
            { number: 2, width: 320, height: 180, preview: "two.html" },
          ],
        });
      }
      if (url.includes("elements.json")) return Response.json([]);
      if (/\/(one|two)\.html$/.test(url))
        return new Response(`<svg><text>${url}</text></svg>`);
      return new Response("missing", { status: 404 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.title = "";
});

describe("DocumentViewer", () => {
  it("uses saved recovery pages for initial links and table source navigation", async () => {
    const onUnitChange = vi.fn();
    render(
      <Viewer
        job={job({
          filename: "deck.pptx",
          pages: 530,
          preview: "progressive-preview/revision-530.html",
        })}
        initialUnit={2}
        onUnitChange={onUnitChange}
      />,
    );
    const selected = await screen.findByTitle("原本プレビュー・スライド 2");
    expect(selected).toHaveAttribute(
      "src",
      "/files/job/two.html?inline_fonts=true",
    );
    expect(original()).toHaveAttribute("src", "about:blank");
    expect(original()).toHaveAttribute("hidden");
    expect(document.getElementById("explanationDisclosure")).toBeNull();
    expect(document.querySelector(".lightweight-preview-notice")).toBeNull();
    expect(onUnitChange).toHaveBeenLastCalledWith(2);
    fireEvent.click(screen.getByRole("button", { name: "前のスライド" }));
    await waitFor(() => expect(onUnitChange).toHaveBeenLastCalledWith(1));
    fireEvent.click(screen.getByRole("tab", { name: "表を操作" }));
    fireEvent.click(await screen.findByRole("button", { name: "表の出典" }));
    await waitFor(() =>
      expect(screen.getByLabelText("スライド")).toHaveValue("2"),
    );
    expect(screen.getByLabelText("プレビューを大きく開く")).toHaveAttribute(
      "href",
      "/files/job/two.html?inline_fonts=true",
    );
  });

  it("never opens the 530-frame legacy preview when recovery metadata fails", async () => {
    slidesMode = "error";
    render(
      <Viewer
        job={job({
          filename: "deck.pptx",
          pages: 530,
          preview: "progressive-preview/revision-530.html",
        })}
      />,
    );
    await screen.findByText("bad slides");
    expect(original()).toHaveAttribute("src", "about:blank");
    expect(original()).toHaveAttribute("hidden");
    expect(screen.getByLabelText("プレビューを大きく開く")).not.toBeVisible();
    expect(screen.getByLabelText("プレビューを大きく開く")).not.toHaveAttribute(
      "href",
    );
  });

  it("keeps page controls available while a recovery page is not yet generated", async () => {
    slidesMode = "empty";
    render(
      <Viewer
        job={job({
          filename: "deck.pptx",
          pages: 530,
          preview: "progressive-preview/revision-1.html",
          state: "partial",
        })}
      />,
    );
    await screen.findByText(
      "このページのプレビューはまだありません。作成済みのページを選んでください。",
    );
    expect(screen.getByLabelText("スライド")).toBeVisible();
    expect(original()).toHaveAttribute("src", "about:blank");
  });

  it("renders each preview target and warning", () => {
    const cases: {
      job: Partial<Job>;
      language?: Language;
      revision?: string;
      src: string;
      sandbox: string;
      title: string;
      className?: string;
      pointer?: string;
      hidden?: boolean;
    }[] = [
      {
        job: { filename: "scan.PDF" },
        src: "/view/job/pdf",
        sandbox: "allow-scripts",
        title: "原本プレビュー",
      },
      {
        job: { filename: "Book.XLSX", pages: 2 },
        src: "/view/job/workbook",
        sandbox: "allow-scripts allow-downloads",
        title: "原本プレビュー",
      },
      {
        job: { filename: "memo.txt", original_filename: "Memo.DOCX" },
        language: "en",
        revision: "a/b",
        src: "/view/job/word?language=en&revision=a%2Fb",
        sandbox: "allow-scripts",
        title: "原本プレビュー",
        className: "word-preview",
      },
      {
        job: { filename: "notes.md" },
        language: "ja",
        revision: "rev",
        src: "/view/job/document?language=ja&revision=rev",
        sandbox: "allow-scripts",
        title: "本文プレビュー",
        className: "word-preview",
      },
      {
        job: { filename: "deck.pptx", preview: "quick.html" },
        src: "/files/job/quick.html",
        sandbox: "",
        title: "原本プレビュー",
        pointer: "none",
      },
      {
        job: { filename: "gone.pdf", preview: null },
        src: "about:blank",
        sandbox: "allow-scripts",
        title: "原本プレビュー",
        hidden: true,
      },
    ];
    for (const item of cases) {
      cleanup();
      render(
        <Viewer
          job={job(item.job)}
          initialLanguage={item.language}
          extensions={
            item.revision
              ? {
                  revision: item.revision,
                  translation: { controls: null, content: null, dialog: null },
                  explanation: {
                    controls: null,
                    panel: null,
                    disclosure: null,
                    opened: false,
                  },
                  menu: null,
                }
              : undefined
          }
        />,
      );
      const frame = original();
      expect(frame.getAttribute("src")).toBe(item.src);
      expect(frame).toHaveAttribute("sandbox", item.sandbox);
      expect(frame).toHaveAttribute("title", item.title);
      if (item.className) expect(frame).toHaveClass(item.className);
      else expect(frame).not.toHaveClass("word-preview");
      if (item.pointer)
        expect(frame).toHaveStyle({ pointerEvents: item.pointer });
      else expect(frame.style.pointerEvents).toBe("");
      if (item.hidden) expect(frame).toHaveAttribute("hidden");
      else expect(frame).not.toHaveAttribute("hidden");
    }
    cleanup();
    render(<Viewer job={job({ preview: null })} />);
    expect(document.getElementById("noPreview")).not.toHaveAttribute("hidden");
    expect(document.getElementById("originalOpen")).toHaveAttribute("hidden");
    cleanup();
    render(
      <Viewer
        job={job({ state: "queued", error: "保存済みのエラー" })}
        message="先にこちら"
      />,
    );
    expect(document.getElementById("viewerMessage")).toHaveTextContent(
      "先にこちら",
    );
    cleanup();
    render(<Viewer job={job({ state: "running", error: "抽出エラー" })} />);
    expect(document.getElementById("viewerMessage")).toHaveTextContent(
      "抽出エラー",
    );
    cleanup();
    render(<Viewer job={job({ state: "queued", error: null })} />);
    expect(document.getElementById("viewerMessage")).toHaveTextContent(
      "待機中です。完了するとプレビューを表示します。",
    );
    cleanup();
    render(<Viewer job={job({ state: "running", error: null })} />);
    expect(document.getElementById("viewerMessage")).toHaveTextContent(
      "抽出中です。完了するとプレビューを表示します。",
    );
    cleanup();
    render(
      <Viewer job={job({ state: "failed", error: null, preview: null })} />,
    );
    expect(document.getElementById("viewerMessage")).toHaveAttribute("hidden");
    expect(document.getElementById("noPreview")).toHaveAttribute("hidden");
  });

  it("switches views, asks a question, and follows the current unit", async () => {
    const onBack = vi.fn();
    const onQuestion = vi.fn();
    const onUnitChange = vi.fn();
    const onView = vi.fn();
    render(
      <Viewer
        onBack={onBack}
        onQuestion={onQuestion}
        onUnitChange={onUnitChange}
        onView={onView}
        navigation={<nav>パンくず</nav>}
        initialUnit={2}
        extensions={{
          revision: "rev",
          translation: {
            controls: <span>訳の操作</span>,
            content: <span>訳の本文</span>,
            dialog: <span>訳のダイアログ</span>,
          },
          explanation: {
            controls: <span>解説の操作</span>,
            panel: <span>解説パネル</span>,
            disclosure: <span>解説の開示</span>,
            opened: true,
          },
          menu: <span>その他メニュー</span>,
        }}
      />,
    );
    expect(document.title).toBe("report.pdf · Docling Desk");
    expect(screen.getByText("パンくず")).toBeInTheDocument();
    expect(screen.getByText("訳の操作")).toBeInTheDocument();
    expect(screen.getByText("解説の操作")).toBeInTheDocument();
    expect(screen.getByText("その他メニュー")).toBeInTheDocument();
    expect(document.getElementById("preview")).toHaveClass("explanation-open");
    expect(document.getElementById("explanationDisclosure")).toBeNull();
    expect(
      screen.getByRole("link", { name: "プレビューを大きく開く" }),
    ).toHaveAttribute("href", "/view/job/pdf");
    fireEvent.click(screen.getByRole("button", { name: "資料一覧へ戻る" }));
    expect(onBack).toHaveBeenCalledOnce();
    fireEvent.click(
      screen.getByRole("button", { name: "この箇所について質問" }),
    );
    expect(onQuestion).toHaveBeenCalledWith("", 1);
    const frame = original();
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "docling-translation-language",
        language: "original",
        revision: "rev",
      }),
      "*",
    );
    expect(post).toHaveBeenCalledWith(
      { type: "docling-pdf-select", jobId: "job", number: 2 },
      "*",
    );
    onUnitChange.mockClear();
    const emit = (
      data: unknown,
      source: MessageEventSource | null = frame.contentWindow,
    ) =>
      act(() => {
        window.dispatchEvent(new MessageEvent("message", { data, source }));
      });
    emit({ type: "docling-unit-current", jobId: "job", number: 3 });
    expect(onUnitChange).toHaveBeenCalledWith(3);
    onUnitChange.mockClear();
    emit({ type: "docling-unit-current", jobId: "job", number: 1 }, window);
    emit(["nope"]);
    emit({ type: "docling-unit-current", jobId: "other", number: 1 });
    emit({ type: "other", jobId: "job", number: 1 });
    emit({ type: "docling-unit-current", jobId: "job", number: "1" });
    emit({ type: "docling-unit-current", jobId: "job", number: 1.5 });
    emit({ type: "docling-unit-current", jobId: "job", number: 0 });
    emit({ type: "docling-unit-current", jobId: "job", number: 9 });
    expect(onUnitChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "表を操作" }));
    expect(onView).toHaveBeenCalledWith("tables");
    expect(await screen.findByText("tables-visible")).toBeInTheDocument();
    post.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "表の出典" }));
    expect(onView).toHaveBeenCalledWith("preview");
    expect(post).toHaveBeenCalledWith(
      { type: "docling-sheet-select", jobId: "job", number: 2 },
      "*",
    );
    fireEvent.click(screen.getByRole("tab", { name: "表を操作" }));
    expect(screen.getByText("tables-visible")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "構造・参照" }));
    fireEvent.click(screen.getByRole("tab", { name: "RAGデータ" }));
    fireEvent.click(screen.getByRole("tab", { name: "原本プレビュー" }));
    expect(screen.getByText("tables-hidden")).toBeInTheDocument();
    expect(document.getElementById("explanationDisclosure")).toBeNull();
  });

  it("rejects units outside the document and hides embedded chrome", () => {
    const onQuestion = vi.fn();
    const { unmount } = render(
      <Viewer
        job={job({ filename: "notes.md", pages: 1 })}
        embedded
        onQuestion={onQuestion}
        initialUnit={5}
      />,
    );
    expect(screen.queryByRole("button", { name: "資料一覧へ戻る" })).toBeNull();
    expect(document.getElementById("originalOpen")).toHaveAttribute("hidden");
    expect(document.getElementById("explanationDisclosure")).toBeNull();
    expect(
      screen.getByRole("tab", { name: "本文プレビュー" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "この箇所について質問" }),
    );
    expect(onQuestion).toHaveBeenCalledWith("", 1);
    const post = vi.spyOn(original().contentWindow!, "postMessage");
    window.dispatchEvent(
      new MessageEvent("message", {
        source: original().contentWindow,
        data: { type: "docling-unit-current", jobId: "job", number: 2 },
      }),
    );
    expect(post).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "docling-pdf-select", number: 2 }),
      "*",
    );
    unmount();
    tablesControl.page = 0;
    render(<Viewer job={job({ pages: 0 })} initialUnit={-3} />);
    fireEvent.click(screen.getByRole("tab", { name: "表を操作" }));
    const sheet = vi.spyOn(original().contentWindow!, "postMessage");
    sheet.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "表の出典" }));
    expect(sheet).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "docling-pdf-select" }),
      "*",
    );
    cleanup();
    tablesControl.page = 1.5;
    render(<Viewer />);
    fireEvent.click(screen.getByRole("tab", { name: "表を操作" }));
    const again = vi.spyOn(original().contentWindow!, "postMessage");
    again.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "表の出典" }));
    expect(again).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "docling-sheet-select" }),
      "*",
    );
  });

  it("selects slides immediately and falls back when slide data is unusable", async () => {
    const onUnitChange = vi.fn();
    render(
      <Viewer
        job={job({
          filename: "deck.pptx",
          slide_layout: true,
          preview: "deck.html",
        })}
        initialUnit={2}
        initialLanguage="ja"
        onUnitChange={onUnitChange}
      />,
    );
    expect(onUnitChange).toHaveBeenCalledWith(2);
    const link = await screen.findByRole("link", {
      name: "プレビューを大きく開く",
    });
    await waitFor(() =>
      expect(link.getAttribute("href")).toContain("/view/job/slides/2"),
    );
    expect(original()).toHaveAttribute("hidden");
    expect(original()).toHaveAttribute("src", "about:blank");
    cleanup();

    slidesMode = "error";
    render(
      <Viewer
        job={job({
          filename: "deck.pptx",
          slide_layout: true,
          preview: "deck.html",
        })}
      />,
    );
    await waitFor(() =>
      expect(original().getAttribute("src")).toBe("/files/job/deck.html"),
    );
    expect(original()).not.toHaveAttribute("hidden");
    cleanup();

    slidesMode = "empty";
    render(
      <Viewer
        job={job({
          filename: "deck.pptx",
          slide_layout: true,
          preview: "deck.html",
        })}
      />,
    );
    expect(
      await screen.findByText(
        "スライド別表示を取得できないため、文書全体のプレビューを表示します。",
      ),
    ).toBeInTheDocument();
    fireEvent.load(original());
  });

  it("shows the tables error boundary", async () => {
    tablesControl.mode = "throw";
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Viewer view="tables" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "表の画面を読み込めませんでした。",
    );
    expect(
      screen.getByRole("button", { name: "再読み込み" }),
    ).toBeInTheDocument();
  });

  it("does not restore removed disclosure banners for partial documents", () => {
    render(
      <Viewer
        job={job({ state: "partial" })}
        extensions={{
          revision: "",
          translation: { controls: null, content: null, dialog: null },
          explanation: {
            controls: null,
            panel: null,
            disclosure: <span>部分開示</span>,
            opened: false,
          },
          menu: null,
        }}
      />,
    );
    expect(screen.queryByText("部分開示")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "構造・参照" }));
    expect(document.getElementById("explanationDisclosure")).toBeNull();
  });
});
