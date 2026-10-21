import { useViewerSource } from "./data-source";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  ActionLink,
  Button,
  DocumentFrame,
  IconButton,
  Tabs,
  Toolbar,
} from "../components/ui";
import { Icon } from "../components/Icons";
import { FeatureBoundary, lazyFeature } from "../components/FeatureBoundary";
import { fileUrl, object } from "../lib/api";
import {
  isDocument,
  isDocx,
  isPdf,
  isSource,
  isTex,
  isXlsx,
  originalName,
} from "../lib/formats";
import {
  isDone,
  isLightweightPreview,
  labels,
  type Job,
  type Language,
  type View,
} from "../lib/types";
import { useSlides } from "../features/Slides";
import { Rag, Structure } from "../features/Resources";
import { usePreviewCopy } from "../features/PreviewCopy";
import { useWordPreview } from "../features/Word";
import "../features/Viewer.css";
const Tables = lazyFeature(() =>
  import("../features/Tables").then((m) => ({ default: m.Tables })),
);
export function DocumentViewer({
  job,
  view,
  onView,
  onBack,
  backLabel = "資料一覧へ戻る",
  message,
  initialLanguage,
  navigation,
  initialUnit,
  extensions,
  embedded = false,
  onUnitChange,
  onQuestion,
}: {
  job: Job;
  view: View;
  onView: (v: View) => void;
  onBack: () => void;
  /** The back control's name; it says where Back goes when that is not the list. */
  backLabel?: string;
  message: string;
  initialLanguage: Language;
  onLanguage: (l: Language) => void;
  navigation?: import("react").ReactNode;
  initialUnit?: number | null;
  embedded?: boolean;
  onUnitChange?: (unit: number | null) => void;
  onQuestion?: (text: string, unit: number) => void;
  extensions?: {
    revision: string;
    translation: {
      controls: import("react").ReactNode;
      content: import("react").ReactNode;
      dialog: import("react").ReactNode;
    };
    explanation: {
      controls: import("react").ReactNode;
      panel: import("react").ReactNode;
      disclosure: import("react").ReactNode;
      opened: boolean;
    };
    menu: import("react").ReactNode;
  };
}) {
  const [tablesVisited, setTablesVisited] = useState(view === "tables");
  useEffect(() => {
    if (view === "tables") setTablesVisited(true);
  }, [view]);
  const original = originalName(job);
  const tex = isTex(original);
  const sourceText = isSource(original);
  const [texMode, setTexMode] = useState<"pdf" | "source">("pdf");
  const texPdf = tex && texMode === "pdf" && !!job.tex_preview;
  const wholeDocument = isDocument(original) && !texPdf;
  const lightweight = isLightweightPreview(job);
  const slideView = job.slide_layout || lightweight;
  const workbook = isXlsx(job.filename),
    pdf = isPdf(job.filename) || texPdf;
  const requestedPage = initialUnit
    ? Math.max(1, Math.min(initialUnit, wholeDocument ? 1 : job.pages || 1))
    : null;
  const [current, setCurrent] = useState<number | null>(
      slideView || wholeDocument
        ? wholeDocument
          ? 1
          : requestedPage || 1
        : null,
    ),
    frame = useRef<HTMLIFrameElement>(null);
  const language = initialLanguage;
  const source = useViewerSource();
  const translation = extensions?.translation || {
    controls: null,
    content: null,
    dialog: null,
  };
  const revision = extensions?.revision || "";
  const explanation = extensions?.explanation || {
    controls: null,
    panel: null,
    disclosure: null,
    opened: false,
  };
  useEffect(() => {
    onUnitChange?.(current);
  }, [current, onUnitChange]);
  const slides = useSlides(
    job,
    view === "preview",
    current || 1,
    setCurrent,
    language,
    revision,
  );
  const word = isDocx(original);
  const { controls: wordControls, notify: notifyWord } = useWordPreview(
    job.id,
    wholeDocument,
    view === "preview",
    frame,
    word ? "Word" : "文書",
  );
  const copyFrames = useCallback(
    () => [frame.current, slides.frame.current],
    [slides.frame],
  );
  const copy = usePreviewCopy(
    job.id,
    `${current}:${slides.selectionRevision}:${language}:${revision}:${view}`,
    copyFrames,
    `${wholeDocument ? "document" : job.slide_layout ? "slide" : workbook ? "sheet" : "page"}-${current || 1}`,
  );
  const frameReady = useRef(false);
  const pendingUnit = useRef(requestedPage);
  const selectUnit = useCallback(
    (number: number) => {
      if (
        !Number.isInteger(number) ||
        number < 1 ||
        number > (wholeDocument ? 1 : job.pages)
      )
        return;
      if (slideView) setCurrent(number);
      frame.current?.contentWindow?.postMessage(
        { type: "docling-sheet-select", jobId: job.id, number },
        "*",
      );
      frame.current?.contentWindow?.postMessage(
        { type: "docling-pdf-select", jobId: job.id, number },
        "*",
      );
    },
    [job.id, slideView, job.pages, wholeDocument],
  );
  const selectPage = useCallback(
    (number: number) => {
      selectUnit(number);
      onView("preview");
    },
    [selectUnit, onView],
  );
  useEffect(() => {
    pendingUnit.current = requestedPage;
    if (requestedPage && (slideView || frameReady.current)) {
      selectUnit(requestedPage);
      pendingUnit.current = null;
    }
  }, [requestedPage, slideView, selectUnit]);
  const notify = useCallback(() => {
    if (slideView) return;
    notifyWord();
    const w = frame.current?.contentWindow;
    w?.postMessage(
      {
        type: "docling-translation-language",
        jobId: job.id,
        language,
        revision: revision,
      },
      "*",
    );
    w?.postMessage({ type: "docling-unit-request", jobId: job.id }, "*");
    if (frameReady.current && pendingUnit.current) {
      selectUnit(pendingUnit.current);
      pendingUnit.current = null;
    }
  }, [job.id, slideView, language, revision, notifyWord, selectUnit]);
  useEffect(() => {
    notify();
  }, [notify]);
  useEffect(() => {
    const message = (e: MessageEvent<unknown>) => {
      const d = e.data;
      if (
        e.source !== frame.current?.contentWindow ||
        !object(d) ||
        d.jobId !== job.id ||
        d.type !== "docling-unit-current" ||
        typeof d.number !== "number" ||
        !Number.isInteger(d.number) ||
        d.number < 1 ||
        d.number > (wholeDocument ? 1 : job.pages)
      )
        return;
      setCurrent(d.number);
    };
    window.addEventListener("message", message);
    return () => window.removeEventListener("message", message);
  }, [job.id, job.pages, wholeDocument]);
  useEffect(() => {
    document.title = `${job.filename} · Docling Desk`;
  }, [job.filename]);
  const preview = job.preview
    ? workbook
      ? `/view/${job.id}/workbook`
      : pdf
        ? `/view/${job.id}/pdf`
        : wholeDocument
          ? `/view/${job.id}/${word ? "word" : sourceText ? "source" : "document"}?language=${language}&revision=${encodeURIComponent(revision)}`
          : fileUrl(job.id, job.preview)
    : "about:blank";
  const previewUrl = job.preview ? source.url(preview) : preview;
  const warning =
    message ||
    job.error ||
    (tex ? job.tex_error : "") ||
    (["queued", "running"].includes(job.state)
      ? `${labels[job.state]}です。完了するとプレビューを表示します。`
      : "");
  return (
    <>
      <section id="detail" aria-label="文書ビューア">
        <Toolbar as="header" id="viewerToolbar" className="viewer-toolbar">
          {navigation}
          {!embedded && (
            <IconButton
              id="backToFiles"
              icon="back"
              label={backLabel}
              onClick={onBack}
            />
          )}
          <span id="filename" title={job.filename}>
            {job.filename}
          </span>
          <Tabs
            label="文書の表示"
            value={view}
            onChange={onView}
            items={[
              {
                id: "preview",
                label:
                  wholeDocument && !word ? "本文プレビュー" : "原本プレビュー",
              },
              { id: "tables", label: "表を操作" },
              { id: "structure", label: "構造・参照" },
              { id: "rag", label: "RAGデータ" },
            ]}
          />
          {slides.controls}
          {wordControls}
          {tex && view === "preview" && (
            <div
              className="slide-controls"
              role="group"
              aria-label="TeXの表示切替"
            >
              <Button
                aria-pressed={texPdf}
                disabled={!job.tex_preview}
                onClick={() => setTexMode("pdf")}
              >
                組版表示
              </Button>
              <Button
                aria-pressed={!texPdf}
                onClick={() => setTexMode("source")}
              >
                TeXソース
              </Button>
            </div>
          )}
          <div className="viewer-actions">
            {view === "preview" && !texPdf && copy.controls}
            {view === "preview" && !texPdf && explanation.controls}
            {!texPdf && translation.controls}
            <ActionLink
              id="originalOpen"
              className="icon-button"
              target="_blank"
              rel="noopener"
              aria-label="プレビューを大きく開く"
              title="プレビューを大きく開く"
              hidden={
                embedded || !job.preview || (lightweight && !slides.active)
              }
              href={
                slides.active
                  ? slides.url
                  : lightweight
                    ? undefined
                    : previewUrl
              }
            >
              <Icon name="open" />
            </ActionLink>
            {view === "preview" && onQuestion && (
              <Button onClick={() => onQuestion(copy.text, current || 1)}>
                この箇所について質問
              </Button>
            )}
            {extensions?.menu}
          </div>
        </Toolbar>
        <div
          id="viewerMessage"
          className="viewer-message"
          role="status"
          hidden={!warning}
        >
          {warning}
        </div>
        <div id="documentPanels">
          <div
            id="preview"
            role="tabpanel"
            aria-labelledby="tab-preview"
            hidden={view !== "preview"}
            className={explanation.opened ? "explanation-open" : ""}
          >
            {!texPdf && explanation.panel}
            {!texPdf && translation.content}
            {slides.content}
            <DocumentFrame
              id="original"
              ref={frame}
              title={
                wholeDocument && !word ? "本文プレビュー" : "原本プレビュー"
              }
              sandbox={
                workbook
                  ? "allow-scripts allow-downloads"
                  : pdf || wholeDocument
                    ? "allow-scripts"
                    : ""
              }
              src={
                lightweight || (job.slide_layout && !slides.fallback)
                  ? "about:blank"
                  : previewUrl
              }
              hidden={
                lightweight ||
                (job.slide_layout && !slides.fallback) ||
                !job.preview
              }
              className={wholeDocument ? "word-preview" : undefined}
              // Passive fallback frames cannot forward drop events. Let the
              // surrounding viewer receive them, as the original UI did.
              style={
                !workbook && !pdf && !wholeDocument
                  ? { pointerEvents: "none" }
                  : undefined
              }
              onLoad={() => {
                frameReady.current = true;
                notify();
              }}
            />
            <p id="noPreview" hidden={!!job.preview || !isDone(job)}>
              プレビューを生成できませんでした。「…」メニューから原文ドキュメントをダウンロードして確認してください。
            </p>
          </div>
          {tablesVisited && (
            <FeatureBoundary
              onRetry={Tables.reset}
              fallback={(retry) => (
                <div
                  id="tables"
                  role="tabpanel"
                  aria-labelledby="tab-tables"
                  hidden={view !== "tables"}
                >
                  <p role="alert">
                    表の画面を読み込めませんでした。もう一度読み込めます。資料の他の表示はそのまま使えます。
                  </p>
                  <Button onClick={retry}>もう一度読み込む</Button>
                </div>
              )}
            >
              <Suspense
                fallback={
                  <div id="tables" hidden={view !== "tables"}>
                    抽出済みの表を読み込んでいます…
                  </div>
                }
              >
                <Tables
                  job={job}
                  visible={view === "tables"}
                  onSource={selectPage}
                />
              </Suspense>
            </FeatureBoundary>
          )}
          <Structure job={job} visible={view === "structure"} />
          <Rag job={job} visible={view === "rag"} />
        </div>
      </section>
      {translation.dialog}
      {copy.dialog}
    </>
  );
}
