import { useEffect, useState } from "react";
import {
  Plate,
  PlateContent,
  PlateElement,
  PlateLeaf,
  type PlateElementProps,
  type PlateLeafProps,
} from "platejs/react";
import { markdownEditor, editedMarkdown, valueSnapshot } from "./plate";
import { rawParts, combineParts, type RawPart } from "./raw-markdown";

function Element(props: PlateElementProps) {
  const kind = props.element.type;
  const tag =
    (
      {
        h1: "h1",
        h2: "h2",
        h3: "h3",
        h4: "h4",
        h5: "h5",
        h6: "h6",
        p: "p",
      } as const
    )[kind as "p"] ?? "div";
  return <PlateElement {...props} as={tag} />;
}
function Strong(props: PlateLeafProps) {
  return <PlateLeaf {...props} as="strong" />;
}
function Emphasis(props: PlateLeafProps) {
  return <PlateLeaf {...props} as="em" />;
}
function Code(props: PlateLeafProps) {
  return <PlateLeaf {...props} as="code" />;
}
function PartEditor({
  raw,
  onChange,
  readOnly,
}: {
  raw: string;
  onChange: (raw: string) => void;
  readOnly: boolean;
}) {
  const [state] = useState(() => {
    const editor = markdownEditor(raw, {
      p: Element,
      h1: Element,
      h2: Element,
      h3: Element,
      h4: Element,
      h5: Element,
      h6: Element,
      bold: Strong,
      italic: Emphasis,
      code: Code,
    });
    return { editor, original: raw, snapshot: valueSnapshot(editor.children) };
  });
  return (
    <Plate
      editor={state.editor}
      readOnly={readOnly}
      onValueChange={() =>
        onChange(editedMarkdown(state.editor, state.original, state.snapshot))
      }
    >
      <div className="formatting" aria-label="書式">
        <button
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => state.editor.tf.toggleMark("bold")}
        >
          太字
        </button>
        <button
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => state.editor.tf.toggleMark("italic")}
        >
          斜体
        </button>
        <button
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => state.editor.tf.undo()}
        >
          戻す
        </button>
        <button
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => state.editor.tf.redo()}
        >
          やり直す
        </button>
      </div>
      <PlateContent aria-label="本文ブロック" />
    </Plate>
  );
}
type AttachmentLoader = (
  id: string,
) => Promise<{ media_type: string; body_base64: string }>;
function AttachmentImage({
  id,
  alt,
  load,
}: {
  id: string;
  alt: string;
  load: AttachmentLoader;
}) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let stopped = false;
    void load(id)
      .then((data) => {
        if (
          !stopped &&
          ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
            data.media_type,
          )
        )
          setSrc("data:" + data.media_type + ";base64," + data.body_base64);
      })
      .catch(() => {});
    return () => {
      stopped = true;
    };
  }, [id, load]);
  return src ? (
    <img src={src} alt={alt} style={{ maxWidth: "100%", maxHeight: 400 }} />
  ) : (
    <small>添付のプレビューは取得できません。</small>
  );
}
export function RawEditor({
  initial,
  onChange,
  readOnly,
  getAttachment,
}: {
  initial: string;
  onChange: (body: string) => void;
  readOnly: boolean;
  getAttachment?: AttachmentLoader;
}) {
  const [parts, setParts] = useState<RawPart[]>(() => rawParts(initial));
  return (
    <div className="rich-body">
      {parts.map((part, index) =>
        part.editable ? (
          <section key={index} className="editable-block">
            <PartEditor
              raw={part.raw}
              readOnly={readOnly}
              onChange={(raw) => {
                const next = parts.map((p, i) =>
                  i === index ? { ...p, raw } : p,
                );
                setParts(next);
                onChange(combineParts(next));
              }}
            />
          </section>
        ) : part.kind === "spacing" ? (
          <span key={index} aria-hidden="true" />
        ) : (
          <section key={index} className="protected-block">
            <small>原文を保持したブロック · ソース編集で変更できます</small>
            <pre>{part.raw}</pre>
            {getAttachment &&
              part.images?.map((image) => (
                <AttachmentImage
                  key={image.attachment_id}
                  id={image.attachment_id}
                  alt={image.alt}
                  load={getAttachment}
                />
              ))}
          </section>
        ),
      )}
    </div>
  );
}
/** Textareas normalize CRLF. Preserve untouched source around the explicit edit. */
export function sourceEdit(original: string, display: string): string {
  const previous = original.replace(/\r\n/g, "\n");
  let start = 0,
    end = 0;
  while (
    start < previous.length &&
    start < display.length &&
    previous[start] === display[start]
  )
    start++;
  while (
    end < previous.length - start &&
    end < display.length - start &&
    previous[previous.length - 1 - end] === display[display.length - 1 - end]
  )
    end++;
  const offset = (index: number) => {
    let raw = 0,
      count = 0;
    while (count < index) {
      if (original[raw] === "\r" && original[raw + 1] === "\n") raw++;
      raw++;
      count++;
    }
    return raw;
  };
  const replacement = display.slice(start, display.length - end);
  return (
    original.slice(0, offset(start)) +
    (original.includes("\r\n")
      ? replacement.replace(/\n/g, "\r\n")
      : replacement) +
    original.slice(offset(previous.length - end))
  );
}
