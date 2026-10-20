import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ActionLink,
  Button,
  Dialog,
  DialogHeading,
  IconButton,
  StatusMessage,
  TextField,
} from "../components/ui";
import { errorText, originalUrl, request } from "../lib/api";
import { withBase } from "../lib/base";
import { assetId, imageMarkdown, type MediaAsset } from "../lib/media";
import type { Job } from "../lib/types";
import "./media-library.css";

export function ImageDetails({
  job,
  onClose,
}: {
  job: Job;
  onClose: () => void;
}) {
  const [reload, setReload] = useState(0);
  const query = useQuery({
    queryKey: ["media", job.id, reload],
    queryFn: () => request<MediaAsset>(`/api/v1/media/${assetId(job.id)}`),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: Infinity,
    gcTime: 0,
  });
  return (
    <Dialog
      className="image-details-dialog"
      id="imageDetails"
      aria-labelledby="imageDetailsTitle"
      open
      onClose={onClose}
    >
      <DialogHeading
        id="imageDetailsTitle"
        title={job.filename}
        onClose={onClose}
      />
      {query.data ? (
        <ImageFields
          key={reload}
          asset={query.data}
          job={job}
          onReload={() => setReload((n) => n + 1)}
        />
      ) : (
        <StatusMessage>
          {query.error ? errorText(query.error) : "画像情報を読み込んでいます…"}
        </StatusMessage>
      )}
      {query.error && (
        <Button onClick={() => void query.refetch()}>再試行</Button>
      )}
    </Dialog>
  );
}

function ImageFields({
  asset,
  job,
  onReload,
}: {
  asset: MediaAsset;
  job: Job;
  onReload: () => void;
}) {
  const cache = useQueryClient(),
    [current, setCurrent] = useState(asset),
    [values, setValues] = useState({
      display_name: asset.display_name,
      description: asset.description,
      tags: asset.tags.join(", "),
      default_alt:
        asset.default_alt || asset.original_filename.replace(/\.[^.]+$/, ""),
      default_credit: asset.default_credit,
      source_url: asset.source_url,
    }),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [conflict, setConflict] = useState(false),
    [imageError, setImageError] = useState(false),
    [zoom, setZoom] = useState<number | null>(null),
    [viewport, setViewport] = useState({ width: 0, height: 0 }),
    stage = useRef<HTMLDivElement>(null),
    drag = useRef<{ x: number; y: number; left: number; top: number } | null>(
      null,
    ),
    copied = useRef<HTMLInputElement>(null),
    saving = useRef(false);
  useEffect(() => {
    if (!stage.current) return;
    const measure = () =>
      setViewport({
        width: stage.current!.clientWidth,
        height: stage.current!.clientHeight,
      });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage.current);
    return () => observer.disconnect();
  }, []);
  const fit = Math.min(
    1,
    viewport.width / asset.width,
    viewport.height / asset.height,
  );
  const ratio = zoom ?? fit;
  const scale = (value: number) => setZoom(Math.max(0.05, Math.min(8, value)));
  const markdown = imageMarkdown(values.default_alt, asset.reference);
  function change(key: keyof typeof values, value: string) {
    setValues((previous) => ({ ...previous, [key]: value }));
    setMessage("");
  }
  async function save() {
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    setMessage("");
    try {
      const next = await request<MediaAsset>(
        `/api/v1/media/${asset.asset_id}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "If-Match": current.metadata_etag,
          },
          body: JSON.stringify({
            ...values,
            tags: values.tags
              .split(",")
              .map((t) => t.trim())
              .filter(Boolean),
          }),
        },
      );
      setCurrent(next);
      setConflict(false);
      setMessage("保存しました。");
      await cache.invalidateQueries({ queryKey: ["library"] });
    } catch (error) {
      setMessage(errorText(error));
      setConflict(true);
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
      setMessage("Markdownをコピーしました。");
    } catch {
      copied.current?.focus();
      copied.current?.select();
      setMessage("選択中のMarkdownをコピーしてください。");
    }
  }
  return (
    <div className="image-details-body">
      <div className="image-details-preview">
        <div
          className="image-viewer-controls"
          role="group"
          aria-label="画像の表示操作"
        >
          <IconButton
            icon="minus"
            label="画像を縮小"
            disabled={imageError || ratio <= 0.05}
            onClick={() => scale(ratio / 1.25)}
          />
          <span aria-live="polite">{Math.round(ratio * 100)}%</span>
          <IconButton
            icon="plus"
            label="画像を拡大"
            disabled={imageError || ratio >= 8}
            onClick={() => scale(ratio * 1.25)}
          />
          <Button disabled={imageError} onClick={() => setZoom(1)}>
            100%
          </Button>
          <Button
            disabled={imageError}
            aria-pressed={zoom === null}
            onClick={() => setZoom(null)}
          >
            全体表示
          </Button>
        </div>
        <div
          className="image-viewer-stage"
          ref={stage}
          tabIndex={0}
          role="region"
          aria-label="画像表示領域"
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            const node = event.currentTarget;
            drag.current = {
              x: event.clientX,
              y: event.clientY,
              left: node.scrollLeft,
              top: node.scrollTop,
            };
            node.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (!drag.current) return;
            event.currentTarget.scrollLeft =
              drag.current.left + drag.current.x - event.clientX;
            event.currentTarget.scrollTop =
              drag.current.top + drag.current.y - event.clientY;
          }}
          onPointerUp={() => {
            drag.current = null;
          }}
          onLostPointerCapture={() => {
            drag.current = null;
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
        >
          {imageError ? (
            <StatusMessage>画像を表示できませんでした。</StatusMessage>
          ) : (
            <div
              className="image-viewer-canvas"
              style={{
                width: Math.max(viewport.width, asset.width * ratio),
                height: Math.max(viewport.height, asset.height * ratio),
              }}
            >
              <img
                src={withBase(asset.reference)}
                alt={values.default_alt}
                draggable={false}
                style={{
                  width: asset.width * ratio,
                  height: asset.height * ratio,
                }}
                onError={() => setImageError(true)}
              />
            </div>
          )}
        </div>
        <span>
          {asset.width} × {asset.height} px
          {asset.animated ? " · アニメーション" : ""}
        </span>
      </div>
      <aside className="image-details-fields">
        <div className="image-markdown-copy">
          <TextField
            ref={copied}
            aria-label="コピーするMarkdown"
            value={markdown}
            readOnly
            onFocus={(e) => e.currentTarget.select()}
          />
          <IconButton
            icon="copy"
            label="Markdownをコピー"
            disabled={imageError}
            onClick={() => void copy()}
          />
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <label>
            表示名
            <TextField
              value={values.display_name}
              maxLength={200}
              required
              onChange={(e) => change("display_name", e.target.value)}
            />
          </label>
          <label>
            説明
            <textarea
              value={values.description}
              maxLength={4000}
              rows={3}
              onChange={(e) => change("description", e.target.value)}
            />
          </label>
          <label>
            タグ<span className="image-field-hint">カンマで区切って入力</span>
            <TextField
              value={values.tags}
              onChange={(e) => change("tags", e.target.value)}
            />
          </label>
          <label>
            代替テキスト
            <TextField
              value={values.default_alt}
              maxLength={1000}
              onChange={(e) => change("default_alt", e.target.value)}
            />
          </label>
          <details>
            <summary>出典情報</summary>
            <label>
              出典URL
              <TextField
                type="url"
                value={values.source_url}
                maxLength={2048}
                onChange={(e) => change("source_url", e.target.value)}
              />
            </label>
            <label>
              出典表記
              <TextField
                value={values.default_credit}
                maxLength={2000}
                onChange={(e) => change("default_credit", e.target.value)}
              />
            </label>
          </details>
          <Button type="submit" disabled={busy}>
            {busy ? "保存中…" : "属性を保存"}
          </Button>
        </form>
        <StatusMessage hidden={!message}>{message}</StatusMessage>
        {conflict && <Button onClick={onReload}>最新情報を読み直す</Button>}
        <dl>
          <dt>原本名</dt>
          <dd>{asset.original_filename}</dd>
          <dt>保存先</dt>
          <dd>{["資料一覧", ...asset.folder_path].join(" / ")}</dd>
          <dt>形式</dt>
          <dd>{asset.media_type}</dd>
          <dt>サイズ</dt>
          <dd>
            {(asset.byte_size / 1024).toLocaleString("ja-JP", {
              maximumFractionDigits: 1,
            })}{" "}
            KiB
          </dd>
        </dl>
        <ActionLink href={withBase(originalUrl(job))}>原本を保存</ActionLink>
      </aside>
    </div>
  );
}
