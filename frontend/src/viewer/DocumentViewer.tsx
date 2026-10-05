import { useViewerSource } from "./data-source";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ActionLink,
  Button,
  DocumentFrame,
  IconButton,
  Tabs,
  Toolbar,
} from "../components/ui";
import { Icon } from "../components/Icons";
import { FeatureBoundary } from "../components/FeatureBoundary";
import { fileUrl, object } from "../lib/api";
import { isDocument } from "../lib/formats";
import {
  isDone,
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
const Tables = lazy(() =>
  import("../features/Tables").then((m) => ({ default: m.Tables })),
);
export function DocumentViewer({
  job,
  view,
  onView,
  onBack,
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
  const wholeDocument = isDocument(job.original_filename || job.filename);
  const workbook = job.filename.toLowerCase().endsWith(".xlsx"),
    pdf = job.filename.toLowerCase().endsWith(".pdf");
  const requestedPage = initialUnit
    ? Math.max(1, Math.min(initialUnit, wholeDocument ? 1 : job.pages || 1))
    : null;
  const [current, setCurrent] = useState<number | null>(
      job.slide_layout || wholeDocument
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
  const word = (job.original_filename || job.filename)
    .toLowerCase()
    .endsWith(".docx");
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
      if (job.slide_layout) setCurrent(number);
      frame.current?.contentWindow?.postMessage(
        { type: "docling-sheet-select", jobId: job.id, number },
        "*",
      );
      frame.current?.contentWindow?.postMessage(
        { type: "docling-pdf-select", jobId: job.id, number },
        "*",
      );
    },
    [job.id, job.slide_layout, job.pages, wholeDocument],
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
    if (requestedPage && (job.slide_layout || frameReady.current)) {
      selectUnit(requestedPage);
      pendingUnit.current = null;
    }
  }, [requestedPage, job.slide_layout, selectUnit]);
  const notify = useCallback(() => {
    if (job.slide_layout) return;
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
  }, [job.id, job.slide_layout, language, revision, notifyWord, selectUnit]);
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
          ? `/view/${job.id}/${word ? "word" : "document"}?language=${language}&revision=${encodeURIComponent(revision)}`
          : fileUrl(job.id, job.preview)
    : "about:blank";
  const previewUrl = job.preview ? source.url(preview) : preview;
  const warning =
    message ||
    job.error ||
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
              label="資料一覧へ戻る"
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
          <div className="viewer-actions">
            {view === "preview" && copy.controls}
            {view === "preview" && explanation.controls}
            {translation.controls}
            <ActionLink
              id="originalOpen"
              className="icon-button"
              target="_blank"
              rel="noopener"
              aria-label="プレビューを大きく開く"
              title="プレビューを大きく開く"
              hidden={embedded || !job.preview}
              href={slides.active ? slides.url : previewUrl}
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
        <p
          id="explanationDisclosure"
          className="explanation-disclosure"
          hidden={embedded || view !== "preview" || !isDone(job)}
        >
          {explanation.disclosure}
        </p>
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
            {explanation.panel}
            {translation.content}
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
                job.slide_layout && !slides.fallback
                  ? "about:blank"
                  : previewUrl
              }
              hidden={(job.slide_layout && !slides.fallback) || !job.preview}
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
              fallback={
                <div
                  id="tables"
                  role="tabpanel"
                  aria-labelledby="tab-tables"
                  hidden={view !== "tables"}
                >
                  <p role="alert">
                    表の画面を読み込めませんでした。画面を再読み込みしてお試しください。
                  </p>
                  <Button onClick={() => location.reload()}>再読み込み</Button>
                </div>
              }
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
