import { useEffect, useRef, useState, useCallback } from "react";
import { toggleList } from "@platejs/list";
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
// Kept here for the standalone editor entry; shared callers import ./source-edit so they do not load the Plate core.
export { sourceEdit } from "./source-edit";

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
  activate,
}: {
  raw: string;
  onChange: (raw: string) => void;
  readOnly: boolean;
  activate: (editor: ReturnType<typeof markdownEditor>) => void;
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
  useEffect(() => activate(state.editor), [activate, state.editor]);
  return (
    <Plate
      editor={state.editor}
      readOnly={readOnly}
      onValueChange={() =>
        onChange(editedMarkdown(state.editor, state.original, state.snapshot))
      }
    >
      <PlateContent
        aria-label="本文ブロック"
        placeholder="本文を書く…"
        onFocusCapture={() => activate(state.editor)}
      />
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
  const [parts, setParts] = useState<RawPart[]>(() => {
    const parsed = rawParts(initial);
    // A newly created document needs an editable paragraph before it has an AST node.
    return initial.trim()
      ? parsed
      : [{ raw: initial, editable: true, kind: "paragraph" }];
  });
  const active = useRef<ReturnType<typeof markdownEditor> | null>(null);
  const activate = useCallback((editor: ReturnType<typeof markdownEditor>) => {
    active.current = editor;
  }, []);
  return (
    <div className="rich-body">
      <div className="formatting" aria-label="書式">
        <select
          aria-label="段落の書式"
          defaultValue=""
          disabled={readOnly}
          onChange={(e) => {
            active.current?.tf.setNodes(
              { type: e.target.value },
              { match: (node) => active.current!.api.isBlock(node) },
            );
            active.current?.tf.focus();
            e.target.value = "";
          }}
        >
          <option value="" disabled>
            段落・見出し
          </option>
          <option value="p">標準</option>
          <option value="h1">見出し1</option>
          <option value="h2">見出し2</option>
          <option value="h3">見出し3</option>
        </select>
        {(
          [
            ["太字", "bold", "B"],
            ["斜体", "italic", "I"],
            ["コード", "code", "</>"],
          ] as const
        ).map(([label, mark, text]) => (
          <button
            type="button"
            key={mark}
            aria-label={label}
            title={label}
            disabled={readOnly}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => active.current?.tf.toggleMark(mark)}
          >
            {text}
          </button>
        ))}
        <button
          type="button"
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            if (active.current)
              toggleList(active.current, { listStyleType: "disc" });
          }}
        >
          箇条書き
        </button>
        <button
          type="button"
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => active.current?.tf.undo()}
        >
          戻す
        </button>
        <button
          type="button"
          disabled={readOnly}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => active.current?.tf.redo()}
        >
          やり直す
        </button>
      </div>
      {parts.map((part, index) =>
        part.editable ? (
          <section key={index} className="editable-block">
            <PartEditor
              raw={part.raw}
              readOnly={readOnly}
              activate={activate}
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
