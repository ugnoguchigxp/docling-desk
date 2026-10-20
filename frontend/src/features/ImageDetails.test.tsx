import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ImageDetails } from "./ImageDetails";
import type { Job } from "../lib/types";
import type { MediaAsset } from "../lib/media";

const asset: MediaAsset = {
  asset_id: "550e8400-e29b-41d4-a716-446655440000",
  display_name: "diagram.png",
  original_filename: "diagram.png",
  reference: `/api/wiki/images/${"a".repeat(64)}.png`,
  sha256: "a".repeat(64),
  media_type: "image/png",
  byte_size: 1024,
  width: 160,
  height: 90,
  animated: false,
  folder_path: ["設計"],
  description: "構成図",
  tags: ["システム"],
  default_alt: "図 [概要]",
  default_credit: "",
  source_url: "",
  metadata_etag: '"v1"',
};
const job: Job = {
  id: "550e8400e29b41d4a716446655440000",
  filename: "diagram.png",
  image: asset,
  folder_id: null,
  state: "success",
  created: 1,
  duration: null,
  pages: 0,
  tables: 0,
  pictures: 1,
  chunks: 0,
  search_chunks: 0,
  rag_policy: null,
  error: null,
  preview: null,
  slide_layout: false,
};
const oldShow = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  "showModal",
);
const oldClose = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  "close",
);
const oldClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
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
        this.removeAttribute("open");
      },
    },
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  for (const [key, original] of [
    ["showModal", oldShow],
    ["close", oldClose],
  ] as const) {
    if (original)
      Object.defineProperty(HTMLDialogElement.prototype, key, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, key);
  }
  if (oldClipboard) Object.defineProperty(navigator, "clipboard", oldClipboard);
  else Reflect.deleteProperty(navigator, "clipboard");
});
function mount(patchStatus = 200) {
  const fetcher = vi.fn(
    async (_url: string, init?: RequestInit) =>
      new Response(
        JSON.stringify(
          init?.method === "PATCH"
            ? patchStatus === 200
              ? { ...asset, metadata_etag: '"v2"' }
              : { detail: "画像情報が更新されました。" }
            : asset,
        ),
        {
          status: init?.method === "PATCH" ? patchStatus : 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ImageDetails job={job} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  return fetcher;
}

test("copies one Markdown line with an icon and keeps attributes separate", async () => {
  const fetcher = mount();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  await screen.findByLabelText("コピーするMarkdown");
  const copy = screen.getByRole("button", { name: "Markdownをコピー" });
  expect(copy.textContent).toBe("");
  fireEvent.click(copy);
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(
      `![図 \\[概要\\]](${asset.reference})`,
    ),
  );
  expect(
    fetcher.mock.calls.filter(([, init]) => init?.method === "PATCH"),
  ).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("代替テキスト"), {
    target: { value: "" },
  });
  expect(screen.getByLabelText("コピーするMarkdown")).toHaveValue(
    `![](${asset.reference})`,
  );
  fireEvent.change(screen.getByLabelText("説明"), {
    target: { value: "更新した説明" },
  });
  fireEvent.click(screen.getByRole("button", { name: "属性を保存" }));
  await screen.findByText("保存しました。");
  const saved = fetcher.mock.calls.find(([, init]) => init?.method === "PATCH");
  expect(saved?.[1]?.headers).toMatchObject({ "If-Match": '"v1"' });
  expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({
    description: "更新した説明",
    default_alt: "",
    tags: ["システム"],
  });
});

test("failed copy selects text and a conflicting save preserves typed input", async () => {
  mount(412);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
  });
  await screen.findByLabelText("コピーするMarkdown");
  fireEvent.click(screen.getByRole("button", { name: "Markdownをコピー" }));
  await screen.findByText("選択中のMarkdownをコピーしてください。");
  const copied = screen.getByLabelText(
    "コピーするMarkdown",
  ) as HTMLInputElement;
  expect(copied.selectionEnd! - copied.selectionStart!).toBe(
    copied.value.length,
  );
  fireEvent.change(screen.getByLabelText("説明"), {
    target: { value: "失わない入力" },
  });
  fireEvent.click(screen.getByRole("button", { name: "属性を保存" }));
  await screen.findByText("画像情報が更新されました。");
  expect(screen.getByLabelText("説明")).toHaveValue("失わない入力");
  expect(
    screen.getByRole("button", { name: "最新情報を読み直す" }),
  ).toBeInTheDocument();
});

test("image viewer switches between actual size, zoom and fit", async () => {
  mount();
  const actual = await screen.findByRole("button", { name: "100%" });
  fireEvent.click(actual);
  const image = screen.getByRole("img", { name: "図 [概要]" });
  expect(image).toHaveStyle({ width: "160px", height: "90px" });
  fireEvent.click(screen.getByRole("button", { name: "画像を拡大" }));
  expect(image).toHaveStyle({ width: "200px", height: "112.5px" });
  fireEvent.click(screen.getByRole("button", { name: "画像を縮小" }));
  expect(image).toHaveStyle({ width: "160px" });
  fireEvent.click(screen.getByRole("button", { name: "全体表示" }));
  expect(screen.getByRole("button", { name: "全体表示" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
