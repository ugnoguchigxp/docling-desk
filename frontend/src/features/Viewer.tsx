import { useEffect, useState } from "react";
import type { ComponentProps } from "react";
import { Button, ActionLink, DisclosureMenu } from "../components/ui";
import { originalUrl } from "../lib/api";
import { withBase } from "../lib/base";
import { isDone } from "../lib/types";
import { isDocument } from "../lib/formats";
import { DocumentViewer } from "../viewer/DocumentViewer";
import { useTranslation } from "./Translation";
import { useExplanation } from "./Explanation";
import { PrintPreview } from "./PrintPreview";

/** Application-only generation and export actions surround the reusable viewer. */
export function Viewer(
  props: Omit<
    ComponentProps<typeof DocumentViewer>,
    "extensions" | "embedded" | "onUnitChange" | "onQuestion"
  >,
) {
  const [current, setCurrent] = useState<number | null>(
    props.job.slide_layout ||
      isDocument(props.job.original_filename || props.job.filename)
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
              <ActionLink id="originalDownload" href={withBase(originalUrl(props.job))}>
                原文ドキュメントをダウンロード
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
