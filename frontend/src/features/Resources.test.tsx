import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Job } from "../lib/types";
import { Rag, Structure } from "./Resources";

const job: Job = {
  id: "job",
  filename: "book.pdf",
  folder_id: null,
  state: "success",
  created: 1,
  duration: 1,
  pages: 2,
  tables: 1,
  pictures: 0,
  chunks: 1,
  search_chunks: 1,
  rag_policy: "context",
  error: null,
  preview: "preview.html",
  slide_layout: false,
};

function renderWith(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

test("shows structure cards, blanks, and load failures", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            label: "見出し",
            ref: "#/texts/0",
            text: "本文",
            pages: [1],
            parent: "#/texts/1",
            captions: ["図1"],
            provenance: [{ page: 1 }],
          },
          {
            label: "図",
            ref: "#/pictures/0",
            text: "",
            pages: [],
            parent: null,
            captions: [],
            provenance: [],
          },
        ]),
      ),
    ),
  );
  renderWith(<Structure job={job} visible />);
  expect(await screen.findByText("本文")).toBeInTheDocument();
  expect(screen.getByText("画像・図要素（本文はJSONで参照）")).toBeInTheDocument();
  expect(screen.getByText(/位置情報なし/)).toBeInTheDocument();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("nope", { status: 500 })),
  );
  renderWith(<Structure job={{ ...job, id: "other" }} visible />);
  expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  const hidden = renderWith(
    <Structure job={{ ...job, state: "failed" }} visible={false} />,
  );
  expect(hidden.container.querySelector("#structure")).toHaveAttribute("hidden");
});

test("switches RAG views and jumps back to a parent chunk", async () => {
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  const policy = {
    image_content: "ocr_text",
    context_chunks: 2,
    search_chunks: 3,
    docling_chunks: 4,
    target_chars: 2000,
    tolerance_chars: 500,
  };
  const chunks = {
    context: [
      {
        id: "parent",
        text: "親",
        headings: ["章"],
        pages: [],
        refs: ["r"],
        source: "book.pdf",
        source_sha256: "abc",
        oversize: true,
        row_range: [1, 2],
      },
    ],
    search: [
      {
        id: "child",
        parent_id: "parent",
        text: "子",
        headings: [],
        pages: [1],
        refs: [],
        context_refs: ["c"],
        relations: [],
        source: "book.pdf",
        source_sha256: "abc",
        oversize: true,
        kind: "row",
      },
    ],
    docling: [
      {
        id: "old",
        unit: "ページ 1",
        text: "旧",
        headings: [],
        pages: [1],
        refs: [],
        source: "book.pdf",
        source_sha256: "abc",
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).endsWith("rag-policy.json"))
        return new Response(JSON.stringify(policy));
      const name = String(url).split("/").pop()!;
      const key = name.startsWith("rag-index")
        ? "search"
        : name.startsWith("rag-docling")
          ? "docling"
          : "context";
      return new Response(chunks[key].map((c) => JSON.stringify(c)).join("\n"));
    }),
  );
  renderWith(<Rag job={job} visible />);
  expect(await screen.findByText(/文脈単位 1 件/)).toBeInTheDocument();
  expect(screen.getByText(/画像から読み取った文字/)).toBeInTheDocument();
  expect(screen.getByText(/検索用の小分けを別に用意/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("チャンクの表示"), {
    target: { value: "search" },
  });
  expect(await screen.findByText(/猶予は500文字/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "親文脈を見る" }));
  await waitFor(() => expect(scroll).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("チャンクの表示"), {
    target: { value: "docling" },
  });
  expect(await screen.findByText(/従来のDocling分割/)).toBeInTheDocument();
  expect(screen.getByText("ページ 1 · 1文字")).toBeInTheDocument();
});

test("reports a missing RAG file and a policy without tolerance", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (String(url).endsWith("rag-policy.json"))
        return new Response(
          JSON.stringify({
            context_chunks: 0,
            search_chunks: 0,
            docling_chunks: 0,
            target_chars: 100,
          }),
        );
      return new Response("", { status: 404 });
    }),
  );
  renderWith(<Rag job={job} visible />);
  expect(await screen.findByText("RAG出力を読み込めませんでした。")).toBeInTheDocument();
  const hidden = renderWith(<Rag job={job} visible={false} />);
  expect(hidden.container.querySelector("#rag")).toHaveAttribute("hidden");
});
