import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { ImageOffIcon } from "lucide-react";
import { errorText, request } from "../lib/api";
import { withBase } from "../lib/base";
import type { MediaAsset } from "../lib/media";
import { isWikiImage } from "./raw-markdown";
import { Button } from "../plate/ui-primitives/button";
import { Input } from "../plate/ui-primitives/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../plate/ui-primitives/dialog";

interface MediaPage {
  items: MediaAsset[];
  next_cursor: string | null;
}

export function ImageLibraryDialog({
  onSelect,
  onClose,
  onRestoreFocus,
}: {
  onSelect: (asset: MediaAsset) => void;
  onClose: () => void;
  onRestoreFocus: () => void;
}) {
  const [search, setSearch] = useState("");
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState("");
  const selection = useRef<AbortController | null>(null);
  useEffect(() => () => selection.current?.abort(), []);
  const query = useInfiniteQuery({
    queryKey: ["media-library", search],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ q: search, limit: "24" });
      if (pageParam) params.set("cursor", pageParam);
      return request<MediaPage>(`/api/v1/media?${params}`, { signal });
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const assets = query.data?.pages.flatMap((page) => page.items) ?? [];

  async function choose(item: MediaAsset) {
    if (selection.current) return;
    const controller = new AbortController();
    selection.current = controller;
    setSelecting(true);
    setError("");
    try {
      // Recheck availability and use current alt text if metadata changed.
      const asset = await request<MediaAsset>(
        `/api/v1/media/${encodeURIComponent(item.asset_id)}`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      if (!isWikiImage(asset.reference))
        throw new Error("画像の参照先を取得できませんでした。");
      onSelect(asset);
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorText(cause));
    } finally {
      if (!controller.signal.aborted) setSelecting(false);
      selection.current = null;
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        className="z-[70] flex max-h-[85dvh] flex-col sm:max-w-3xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onRestoreFocus();
        }}
      >
        <DialogHeader>
          <DialogTitle>資料一覧から画像を選ぶ</DialogTitle>
          <DialogDescription>
            画像を選ぶと、本文のカーソル位置に挿入します。
          </DialogDescription>
        </DialogHeader>
        <Input
          type="search"
          aria-label="資料の画像を検索"
          placeholder="名前・説明・タグで検索"
          value={search}
          disabled={selecting}
          onChange={(event) => {
            setSearch(event.target.value);
            setError("");
          }}
        />
        <div
          className="min-h-24 overflow-y-auto"
          aria-busy={query.isFetching || selecting}
        >
          {query.isPending && <p role="status">画像を読み込んでいます…</p>}
          {query.isError && (
            <div role="alert">
              <p>{errorText(query.error)}</p>
              <Button variant="outline" onClick={() => void query.refetch()}>
                再試行
              </Button>
            </div>
          )}
          {!query.isPending && !query.isError && assets.length === 0 && (
            <p role="status">
              {search
                ? "条件に合う画像がありません。"
                : "資料一覧に画像がありません。画像ボタンからアップロードできます。"}
            </p>
          )}
          <ul className="m-0 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3">
            {assets.map((asset) => (
              <li key={asset.asset_id}>
                <button
                  type="button"
                  aria-label={asset.display_name}
                  disabled={selecting}
                  onClick={() => void choose(asset)}
                  className="flex h-full w-full flex-col gap-1 rounded-md border p-2 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                >
                  <LibraryThumbnail asset={asset} />
                  <span className="w-full truncate text-sm font-medium">
                    {asset.display_name}
                  </span>
                  <span className="w-full truncate text-xs text-muted-foreground">
                    {asset.folder_path.join(" / ") || "資料一覧"}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {asset.width} × {asset.height} px
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {query.hasNextPage && (
            <Button
              className="mt-3"
              variant="outline"
              disabled={query.isFetching || selecting}
              onClick={() => void query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? "読み込み中…" : "さらに表示"}
            </Button>
          )}
        </div>
        {selecting && <p role="status">画像を確認しています…</p>}
        {error && <p role="alert">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            キャンセル
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function LibraryThumbnail({ asset }: { asset: MediaAsset }) {
  const [failed, setFailed] = useState(false);
  return (
    <span className="flex h-28 w-full items-center justify-center rounded bg-muted">
      {failed ? (
        <ImageOffIcon aria-label="プレビューなし" />
      ) : (
        <img
          src={withBase(
            `/api/v1/media/${encodeURIComponent(asset.asset_id)}/thumbnail`,
          )}
          alt=""
          loading="lazy"
          className="h-full w-full object-contain"
          onError={() => setFailed(true)}
        />
      )}
    </span>
  );
}
