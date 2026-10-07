import { useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogHeading,
  DocumentFrame,
  SelectField,
  StatusMessage,
  TextField,
} from "../components/ui";
import { errorText } from "../lib/api";
import { withBase } from "../lib/base";
import { isDocument } from "../lib/formats";
import { isLightweightPreview, type Job, type Language } from "../lib/types";
import {
  printUnits,
  renderPrintDocument,
  selectPrintUnits,
  type PrintSettings,
  type PrintUnit,
} from "./print-document";
import "./PrintPreview.css";

export function PrintPreview({
  job,
  current,
  language,
  revision,
  onClose,
}: {
  job: Job;
  current: number;
  language: Language;
  revision: string;
  onClose: () => void;
}) {
  const sourceJob = useMemo(
    () => ({
      id: job.id,
      filename: job.filename,
      original_filename: job.original_filename,
      preview: job.preview,
      slide_layout: job.slide_layout,
      pages: job.pages,
    }),
    [
      job.id,
      job.filename,
      job.original_filename,
      job.preview,
      job.slide_layout,
      job.pages,
    ],
  );
  const [retry, setRetry] = useState(0);
  const sourceKey = JSON.stringify([sourceJob, retry]);
  const [source, setSource] = useState<{
    key: string;
    units?: PrintUnit[];
    error?: string;
  }>();
  const units = source?.key === sourceKey ? source.units : undefined;
  const extracted = units?.some((u) => u.extracted) || false;
  const fixed =
    !!job.preview &&
    !extracted &&
    /\.(pdf|pptx)$/i.test(job.original_filename || job.filename);
  const whole = extracted || isDocument(job.original_filename || job.filename);
  const [scope, setScope] = useState(
      isLightweightPreview(job) ? "current" : "all",
    ),
    [range, setRange] = useState("");
  const [settings, setSettings] = useState<PrintSettings>({
    paper: fixed ? "source" : "a4",
    landscape: false,
    margin: 10,
  });
  const [frameState, setFrameState] = useState<{
    retry: number;
    error?: string;
  }>();
  const frameLoaded = frameState?.retry === retry && !frameState.error;
  const renderKey = JSON.stringify([
    sourceKey,
    scope,
    current,
    range,
    settings,
    language,
    revision,
  ]);
  const [result, setResult] = useState<{
    key: string;
    ready?: boolean;
    error?: string;
  }>();
  const error =
    (source?.key === sourceKey && source.error) ||
    (frameState?.retry === retry && frameState.error) ||
    (result?.key === renderKey && result.error) ||
    "";
  const ready =
    !!units?.length &&
    frameLoaded &&
    result?.key === renderKey &&
    !!result.ready &&
    !error;
  const [zoom, setZoom] = useState("fit");
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    const signal = AbortSignal.any([
      controller.signal,
      AbortSignal.timeout(45000),
    ]);
    printUnits(sourceJob, signal)
      .then((units) => {
        if (!signal.aborted) setSource({ key: sourceKey, units });
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setSource({ key: sourceKey, error: errorText(e) });
      });
    return () => controller.abort();
  }, [sourceJob, sourceKey]);
  useEffect(() => {
    const timer = setTimeout(
      () =>
        setFrameState((state) =>
          state?.retry === retry
            ? state
            : {
                retry,
                error:
                  "印刷用の画面を読み込めませんでした。再読み込みしてください。",
              },
        ),
      45000,
    );
    return () => clearTimeout(timer);
  }, [retry]);
  useEffect(() => {
    if (!frameLoaded || !units?.length) return;
    let doc: Document | null;
    try {
      doc = frame.current?.contentDocument || null;
      if (!doc)
        throw new Error(
          "印刷用の画面にアクセスできません。画面を更新してください。",
        );
    } catch (e) {
      setResult({ key: renderKey, error: errorText(e) });
      return;
    }
    const controller = new AbortController();
    const signal = AbortSignal.any([
      controller.signal,
      AbortSignal.timeout(45000),
    ]);
    setResult({ key: renderKey });
    let selected: PrintUnit[];
    try {
      selected = selectPrintUnits(
        units,
        extracted ? "all" : scope,
        current,
        range,
      );
      if (!selected.length) throw new Error("印刷するページを選んでください。");
    } catch (e) {
      setResult({ key: renderKey, error: errorText(e) });
      return;
    }
    renderPrintDocument(
      doc,
      sourceJob,
      selected,
      settings,
      language,
      revision,
      signal,
    )
      .then(() => {
        if (!signal.aborted) setResult({ key: renderKey, ready: true });
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setResult({ key: renderKey, error: errorText(e) });
      });
    return () => controller.abort();
  }, [
    frameLoaded,
    units,
    scope,
    current,
    range,
    settings,
    sourceJob,
    language,
    revision,
    renderKey,
    extracted,
  ]);
  useEffect(() => {
    const node = frame.current;
    if (!node || !ready) return;
    const fit = () => {
      const doc = node.contentDocument;
      if (!doc) return;
      const widths = [...doc.querySelectorAll<HTMLElement>(".print-unit")].map(
        (p) => p.offsetWidth,
      );
      const ratio =
        zoom === "fit"
          ? Math.min(
              1,
              Math.max(0.1, (node.clientWidth - 48) / Math.max(...widths)),
            )
          : Number(zoom);
      doc.documentElement.style.setProperty("--preview-scale", String(ratio));
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ready, zoom]);
  const sourcePaper = fixed && settings.paper === "source";
  return (
    <Dialog
      open
      onClose={onClose}
      id="printPreviewDialog"
      aria-labelledby="printPreviewTitle"
    >
      <DialogHeading
        id="printPreviewTitle"
        title="プリントプレビュー"
        onClose={onClose}
      />
      <p className="print-filename">{job.filename}</p>
      <div className="print-preview-layout">
        <aside className="print-settings" aria-label="印刷設定">
          {!whole && (
            <>
              <label>
                印刷範囲
                <SelectField
                  aria-label="印刷範囲"
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                >
                  <option value="all">すべて</option>
                  <option value="current">
                    現在の{fixed ? "ページ・スライド" : "シート"}（{current}）
                  </option>
                  <option value="range">範囲を指定</option>
                </SelectField>
              </label>
              {scope === "range" && (
                <label>
                  範囲
                  <TextField
                    aria-label="印刷する範囲"
                    value={range}
                    placeholder="1-3, 5"
                    onChange={(e) => setRange(e.target.value)}
                  />
                </label>
              )}
            </>
          )}
          <label>
            用紙
            <SelectField
              aria-label="印刷の用紙"
              value={
                sourcePaper
                  ? "source"
                  : settings.paper === "source"
                    ? "a4"
                    : settings.paper
              }
              onChange={(e) => {
                const paper = e.currentTarget.value as PrintSettings["paper"];
                setSettings((s) => ({
                  ...s,
                  paper,
                }));
              }}
            >
              {fixed && <option value="source">元のページサイズ</option>}
              <option value="a4">A4</option>
              <option value="letter">レター</option>
            </SelectField>
          </label>
          <label>
            向き
            <SelectField
              aria-label="印刷の向き"
              disabled={sourcePaper}
              value={
                sourcePaper
                  ? "source"
                  : settings.landscape
                    ? "landscape"
                    : "portrait"
              }
              onChange={(e) => {
                const landscape = e.currentTarget.value === "landscape";
                setSettings((s) => ({
                  ...s,
                  landscape,
                }));
              }}
            >
              {sourcePaper && <option value="source">原本に合わせる</option>}
              <option value="portrait">縦</option>
              <option value="landscape">横</option>
            </SelectField>
          </label>
          <label>
            余白
            <SelectField
              aria-label="印刷の余白"
              disabled={sourcePaper}
              value={sourcePaper ? 0 : settings.margin}
              onChange={(e) => {
                const margin = Number(e.currentTarget.value);
                setSettings((s) => ({ ...s, margin }));
              }}
            >
              <option value={0}>なし</option>
              <option value={10}>10 mm</option>
              <option value={20}>20 mm</option>
            </SelectField>
          </label>
          <label>
            プレビューの倍率
            <SelectField
              aria-label="プリントプレビューの倍率"
              value={zoom}
              onChange={(e) => setZoom(e.target.value)}
            >
              <option value="fit">幅に合わせる</option>
              <option value="0.5">50%</option>
              <option value="0.75">75%</option>
              <option value="1">100%</option>
            </SelectField>
          </label>
          <p>
            {extracted || language === "original"
              ? "原文"
              : language === "ja"
                ? "日本語表示"
                : "英語表示"}
            を印刷します。
          </p>
          {extracted && (
            <p role="note">
              原本プレビューがないため、抽出した本文・表・画像を印刷します。配置は原本と異なります。
            </p>
          )}
          <p>
            {fixed
              ? "1ページ・1スライドを1枚に配置します。"
              : "本文は縦に続けて表示します。改ページはブラウザーの印刷画面で確認できます。"}
          </p>
          <p>
            「印刷・PDF保存」を押し、印刷先でプリンターまたは「PDFに保存」を選んでください。
          </p>
          <Button
            className="primary"
            disabled={!ready || !!error}
            onClick={() => {
              try {
                const window = frame.current?.contentWindow;
                if (!window)
                  throw new Error("印刷用の画面を再読み込みしてください。");
                window.print();
              } catch (e) {
                setResult({ key: renderKey, error: errorText(e) });
              }
            }}
          >
            印刷・PDF保存
          </Button>
          {error ? (
            <>
              <StatusMessage role="alert">{error}</StatusMessage>
              <Button
                onClick={() => {
                  setRetry((n) => n + 1);
                }}
              >
                再読み込み
              </Button>
            </>
          ) : (
            <StatusMessage>
              {ready
                ? "印刷の準備ができました。"
                : "印刷用の文書を読み込んでいます…"}
            </StatusMessage>
          )}
        </aside>
        <DocumentFrame
          key={retry}
          ref={frame}
          id="printPreviewFrame"
          title="印刷する文書"
          sandbox="allow-same-origin allow-modals"
          src={withBase("/static/frontend/print.html")}
          onLoad={() => setFrameState({ retry })}
          onError={() =>
            setFrameState({
              retry,
              error:
                "印刷用の画面を読み込めませんでした。再読み込みしてください。",
            })
          }
        />
      </div>
    </Dialog>
  );
}
