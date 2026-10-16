import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { MediaAsset } from "../lib/media";
import { ImageLibraryDialog } from "./ImageLibraryDialog";

const clients: QueryClient[] = [];
const image = (id = "first"): MediaAsset => ({
  asset_id: id,
  display_name: `${id}.png`,
  original_filename: `${id}.png`,
  folder_path: ["画像"],
  metadata_etag: '"saved"',
  default_alt: "元の説明",
  default_credit: "",
  source_url: "",
  description: "",
  tags: [],
  reference: `/api/wiki/images/${"a".repeat(64)}.png`,
  sha256: "a".repeat(64),
  media_type: "image/png",
  byte_size: 10,
  width: 480,
  height: 220,
  animated: false,
});
const reply = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const onSelect = vi.fn(),
    onClose = vi.fn();
  return {
    onSelect,
    onClose,
    ...render(
      <QueryClientProvider client={client}>
        <ImageLibraryDialog
          onSelect={onSelect}
          onClose={onClose}
          onRestoreFocus={vi.fn()}
        />
      </QueryClientProvider>,
    ),
  };
}

it("pages through all folders, searches by the API, and uses fresh metadata without uploading", async () => {
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    expect(init?.method ?? "GET").toBe("GET");
    const url = new URL(input, "http://localhost");
    if (url.pathname.endsWith("/found"))
      return reply({ ...image("found"), default_alt: "更新された説明" });
    if (url.searchParams.get("q"))
      return reply({ items: [image("found")], next_cursor: null });
    return reply(
      url.searchParams.has("cursor")
        ? { items: [image("second")], next_cursor: null }
        : { items: [image()], next_cursor: "next-page" },
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const { onSelect } = setup();
  await screen.findByRole("button", { name: "first.png" });
  fireEvent.click(screen.getByRole("button", { name: "さらに表示" }));
  await screen.findByRole("button", { name: "second.png" });
  expect(
    fetcher.mock.calls.some(([url]) => url.includes("cursor=next-page")),
  ).toBe(true);
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "説明で検索" },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "found.png" }),
  );
  await waitFor(() => expect(onSelect).toHaveBeenCalledOnce());
  expect(onSelect.mock.calls[0][0]).toMatchObject({
    default_alt: "更新された説明",
    reference: image().reference,
  });
});

it("shows search errors with a retry, then explains an empty library or empty result", async () => {
  let failing = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      failing
        ? reply({ detail: "画像を読み込めません。" }, 503)
        : reply({ items: [], next_cursor: null }),
    ),
  );
  setup();
  await screen.findByText("画像を読み込めません。", { exact: true });
  failing = false;
  fireEvent.click(screen.getByRole("button", { name: "再試行" }));
  await screen.findByText(
    "資料一覧に画像がありません。画像ボタンからアップロードできます。",
    { exact: true },
  );
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "missing" },
  });
  await screen.findByText("条件に合う画像がありません。", { exact: true });
});

it("does not insert a deleted image or an invalid reference and permits another choice", async () => {
  let state: "deleted" | "invalid" | "valid" = "deleted";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) =>
      input.includes("?")
        ? reply({ items: [image()], next_cursor: null })
        : state === "deleted"
          ? reply({ detail: "画像が見つかりません。" }, 404)
          : reply({
              ...image(),
              reference:
                state === "invalid" ? "javascript:invalid" : image().reference,
            }),
    ),
  );
  const { onSelect } = setup();
  const button = await screen.findByRole("button", { name: "first.png" });
  fireEvent.error(screen.getByAltText(""));
  expect(screen.getByLabelText("プレビューなし")).toBeInTheDocument();
  fireEvent.click(button);
  await screen.findByText("画像が見つかりません。", { exact: true });
  expect(onSelect).not.toHaveBeenCalled();
  state = "invalid";
  fireEvent.click(button);
  await screen.findByText("画像の参照先を取得できませんでした。", {
    exact: true,
  });
  expect(onSelect).not.toHaveBeenCalled();
  state = "valid";
  fireEvent.click(button);
  await waitFor(() => expect(onSelect).toHaveBeenCalledOnce());
});

it("cancels an in-flight selection when closed and ignores a late response", async () => {
  let resolveDetail!: (value: Response) => void;
  let signal: AbortSignal | null | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, options?: RequestInit) => {
      if (input.includes("?"))
        return reply({ items: [image()], next_cursor: null });
      signal = options?.signal;
      return new Promise<Response>((resolve) => {
        resolveDetail = resolve;
      });
    }),
  );
  const { onSelect, unmount } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "first.png" }));
  await screen.findByText("画像を確認しています…", { exact: true });
  unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => resolveDetail(reply(image())));
  expect(onSelect).not.toHaveBeenCalled();
});
