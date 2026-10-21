import { Suspense, useEffect, useState } from "react";
import type { ComponentProps } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FeatureBoundary, lazyFeature } from "../components/FeatureBoundary";
import {
  Button,
  ActionLink,
  DisclosureMenu,
  IconButton,
  StatusMessage,
  Toolbar,
} from "../components/ui";
import { originalUrl } from "../lib/api";
import { queryKeys } from "../lib/query-keys";
import { withBase } from "../lib/base";
import { isDone, isLightweightPreview } from "../lib/types";
import { isDocument, originalName, viewKind } from "../lib/formats";
import { DocumentViewer } from "../viewer/DocumentViewer";
import { useTranslation } from "./Translation";
import { useExplanation } from "./Explanation";
import { DiagramViewer } from "./DiagramViewer";
import { PrintPreview } from "./PrintPreview";

// Editing a standalone diagram is a different screen from reading documents.
const DiagramEditor = lazyFeature(() =>
  import("./DiagramEditor").then((m) => ({ default: m.DiagramEditor })),
);

/** Application-only generation and export actions surround the reusable viewer. */
type ViewerProps = Omit<
  ComponentProps<typeof DocumentViewer>,
  "extensions" | "embedded" | "onUnitChange" | "onQuestion"
>;
export function Viewer(props: ViewerProps) {
  switch (viewKind(props.job)) {
    case "excalidraw":
      return <ExcalidrawViewer {...props} />;
    case "drawio":
      return <DiagramViewer {...props} />;
    default:
      return <ExtractedViewer {...props} />;
  }
}
/** Standalone Excalidraw files are edited, not extracted or previewed. */
function ExcalidrawViewer({
  job,
  onBack,
  backLabel = "資料一覧へ戻る",
  message,
  navigation,
}: ViewerProps) {
  const cache = useQueryClient();
  useEffect(() => {
    document.title = `${job.filename} · Docling Desk`;
  }, [job.filename]);
  return (
    <section id="detail" aria-label="図表エディター">
      <Toolbar as="header" id="viewerToolbar" className="viewer-toolbar">
        {navigation}
        <IconButton
          id="backToFiles"
          icon="back"
          label={backLabel}
          onClick={onBack}
        />
        <span id="filename" title={job.filename}>
          {job.filename}
        </span>
      </Toolbar>
      {message && (
        <p className="viewer-message" role="status">
          {message}
        </p>
      )}
      <FeatureBoundary
        onRetry={DiagramEditor.reset}
        fallback={(retry) => (
          <StatusMessage
            kind="error"
            recovery={[{ label: "もう一度読み込む", onClick: retry }]}
          >
            図表エディターを読み込めませんでした。保存済みの図は変わりません。
          </StatusMessage>
        )}
      >
        <Suspense
          fallback={
            <StatusMessage kind="loading">
              図表エディターを読み込み中…
            </StatusMessage>
          }
        >
          <DiagramEditor
            job={job}
            onChanged={() =>
              void cache.invalidateQueries({ queryKey: queryKeys.library })
            }
          />
        </Suspense>
      </FeatureBoundary>
    </section>
  );
}
function ExtractedViewer(props: ViewerProps) {
  const [current, setCurrent] = useState<number | null>(
    props.job.slide_layout ||
      isLightweightPreview(props.job) ||
      isDocument(originalName(props.job))
      ? props.initialUnit || 1
      : null,
  );
  const [language, setLanguage] = useState(props.initialLanguage);
  const [printOpened, setPrintOpened] = useState(false);
  useEffect(() => {
    if (!printOpened) return;
    const trigger = document.querySelector<HTMLElement>("#saveMenu summary");
    return () => trigger?.focus();
  }, [printOpened]);
  const onLanguage = (next: typeof language) => {
    setLanguage(next);
    props.onLanguage(next);
  };
  const translation = useTranslation(
    props.job,
    current || 1,
    language,
    onLanguage,
  );
  const explanation = useExplanation(props.job, current);
  return (
    <>
      <DocumentViewer
        {...props}
        initialLanguage={language}
        onLanguage={onLanguage}
        onUnitChange={setCurrent}
        extensions={{
          revision: translation.revision,
          translation,
          explanation,
          menu: (
            <DisclosureMenu
              id="saveMenu"
              label="文書メニュー"
              icon="more"
              onKeyDown={(e) => {
                if (e.key === "Escape" && e.currentTarget.open) {
                  e.currentTarget.open = false;
                  e.currentTarget.querySelector("summary")?.focus();
                }
              }}
              onClickCapture={(e) => {
                if (
                  e.target instanceof Element &&
                  e.target.closest("button, a")
                )
                  e.currentTarget.querySelector("summary")?.focus();
              }}
            >
              <Button
                id="printPreviewOpen"
                disabled={!props.job.preview && !isDone(props.job)}
                onClick={() => setPrintOpened(true)}
              >
                プリントプレビュー
              </Button>
              <ActionLink
                id="originalDownload"
                href={withBase(originalUrl(props.job))}
              >
                原本を保存
              </ActionLink>
            </DisclosureMenu>
          ),
        }}
      />
      {printOpened && (
        <PrintPreview
          job={props.job}
          current={current || 1}
          language={language}
          revision={translation.revision}
          onClose={() => setPrintOpened(false)}
        />
      )}
    </>
  );
}
