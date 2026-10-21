import { Suspense } from "react";
import { FeatureBoundary, lazyFeature } from "../components/FeatureBoundary";
import type { EditorDocument } from "./WikiPlateEditor";
// The Plate kit is the largest part of the app. It is fetched when an article
// or draft is opened in it, never for the library or a plain article.
const FullEditor = lazyFeature(async () => ({
  default: (await import("./WikiPlateEditor")).WikiPlateEditor,
}));
const ignore = () => {};
export function WikiPlate(props: {
  initial: string;
  document?: EditorDocument | null;
  onChange?: (body: string, document: EditorDocument) => void;
  onBusy?: (busy: boolean) => void;
  readOnly: boolean;
  preview?: boolean;
  outline?: { title: string; level: number; anchor: string }[];
  renderedHtml?: string;
}) {
  return (
    <FeatureBoundary
      onRetry={FullEditor.reset}
      fallback={(retry) => (
        <div role="alert">
          <p>
            エディターを表示できませんでした。内容は変わりません。もう一度読み込むと、現在の内容から開き直します。
          </p>
          <button type="button" onClick={retry}>
            もう一度読み込む
          </button>
        </div>
      )}
    >
      <Suspense fallback={<p role="status">エディターを読み込み中…</p>}>
        <FullEditor onChange={ignore} onBusy={ignore} {...props} />
      </Suspense>
    </FeatureBoundary>
  );
}
