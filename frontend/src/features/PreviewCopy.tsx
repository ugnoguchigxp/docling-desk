import { useEffect, useRef, useState } from "react";
import { Button, Dialog, DialogHeading, IconButton } from "../components/ui";
import { object } from "../lib/api";
import { useAsyncScope } from "../lib/async-scope";

export function usePreviewCopy(
  jobId: string,
  resetKey: string,
  frames: () => (HTMLIFrameElement | null)[],
  unitId: string,
) {
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const [manual, setManual] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const { start: startCopy, invalidate } = useAsyncScope(resetKey);
  useEffect(() => {
    invalidate();
    setText("");
    setNotice("");
    setManual("");
    for (const frame of frames())
      frame?.contentWindow?.postMessage(
        { type: "docling-selection-clear", jobId },
        "*",
      );
    const receive = (e: MessageEvent<unknown>) => {
      const d = e.data;
      if (
        !object(d) ||
        d.type !== "docling-text-selection" ||
        d.jobId !== jobId ||
        d.unitId !== unitId ||
        typeof d.text !== "string" ||
        d.text.length > 2_000_000 ||
        !frames().some(
          (f) => f && !f.closest("[hidden]") && f.contentWindow === e.source,
        )
      )
        return;
      invalidate();
      setText(d.text);
      setNotice("");
    };
    window.addEventListener("message", receive);
    return () => {
      invalidate();
      window.removeEventListener("message", receive);
    };
  }, [jobId, resetKey, frames, unitId, invalidate]);
  useEffect(() => {
    if (manual) textarea.current?.select();
  }, [manual]);
  const copy = async () => {
    if (!text) return;
    const isCurrent = startCopy();
    try {
      await navigator.clipboard.writeText(text);
      if (isCurrent()) setNotice("コピーしました。");
    } catch {
      if (isCurrent()) setManual(text);
    }
  };
  return {
    text,
    controls: (
      <>
        <IconButton
          id="previewCopy"
          icon="copy"
          label="選択した文字をコピー"
          disabled={!text}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => void copy()}
        />
        <span className="preview-copy-status" role="status" hidden={!notice}>
          {notice}
        </span>
      </>
    ),
    dialog: (
      <Dialog
        id="previewCopyFallback"
        className="library-dialog"
        open={!!manual}
        onClose={() => setManual("")}
        aria-labelledby="previewCopyTitle"
      >
        <DialogHeading
          id="previewCopyTitle"
          title="選択した文字をコピー"
          onClose={() => setManual("")}
        />
        <p>選択された文字を⌘C／Ctrl+Cでコピーしてください。</p>
        <textarea
          ref={textarea}
          aria-label="コピーする文字"
          readOnly
          value={manual}
        />
        <div className="dialog-actions">
          <Button onClick={() => setManual("")}>閉じる</Button>
        </div>
      </Dialog>
    ),
  };
}
