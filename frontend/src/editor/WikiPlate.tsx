import { lazy, Suspense } from "react";
import type { EditorDocument } from "./WikiPlateEditor";
const FullEditor = lazy(async () => ({
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
    <Suspense fallback={<p role="status">エディターを読み込み中…</p>}>
      <FullEditor onChange={ignore} onBusy={ignore} {...props} />
    </Suspense>
  );
}
