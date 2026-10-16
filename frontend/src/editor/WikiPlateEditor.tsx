import "./plate-browser";
import { useCallback, useEffect, useRef, useState } from "react";
import { NodeApi, type Value, type TLinkElement } from "platejs";
import {
  createPlateEditor,
  createPlatePlugin,
  Plate,
  PlateElement,
  type PlateElementProps,
  useEditorRef,
  usePluginOption,
} from "platejs/react";
import { AIChatPlugin } from "@platejs/ai/react";
import { MarkdownPlugin } from "@platejs/markdown";
import { Toaster } from "sonner";
import { EditorKit } from "../plate/components/editor/editor-kit";
import {
  discussionPlugin,
  type TDiscussion,
} from "../plate/components/editor/plugins/discussion-kit";
import { Editor, EditorContainer } from "../plate/ui/editor";
import { TooltipProvider } from "../plate/ui-primitives/tooltip";
import { UploadActivity } from "../plate/hooks/use-upload-file";
import { valueSnapshot } from "./plate";
import { preservedMarkdown } from "./preserved-markdown";
import { HeadingElement } from "../plate/ui/heading-node";
import { LinkElement } from "../plate/ui/link-node";
import "./wiki-plate.css";

export interface EditorDocument {
  version: 1;
  value: Value;
  discussions: TDiscussion[];
}
function RawMarkdown(props: PlateElementProps) {
  return (
    <PlateElement {...props}>
      <div
        contentEditable={false}
        className="rounded border bg-muted p-3 text-sm"
      >
        <small>原文を保持したブロック · Markdownから編集できます</small>
        <pre className="whitespace-pre-wrap">{String(props.element.raw)}</pre>
      </div>
      {props.children}
    </PlateElement>
  );
}
const RawPlugin = createPlatePlugin({
  key: "raw_markdown",
  node: { isElement: true, isVoid: true, component: RawMarkdown },
});

function DocumentChanges({
  onChange,
  onBusy,
  source,
}: {
  source: ReturnType<typeof preservedMarkdown>;
  onChange: (body: string, document: EditorDocument) => void;
  onBusy: (busy: boolean) => void;
}) {
  const editor = useEditorRef();
  const discussions = usePluginOption(discussionPlugin, "discussions");
  const previousDiscussions = useRef(discussions);
  const chat = usePluginOption(AIChatPlugin, "chat");
  const aiOpen = usePluginOption(AIChatPlugin, "open");
  useEffect(() => {
    onBusy(
      !!aiOpen || chat?.status === "submitted" || chat?.status === "streaming",
    );
  }, [aiOpen, chat?.status, onBusy]);
  useEffect(() => {
    if (previousDiscussions.current === discussions) return;
    previousDiscussions.current = discussions;
    // Discussion updates do not necessarily change Slate's value.
    onChange(source.serialize(editor.children), {
      version: 1,
      value: editor.children,
      discussions,
    });
  }, [discussions, editor, onChange, source]);
  return null;
}

export function WikiPlateEditor({
  initial,
  document,
  onChange,
  onBusy,
  readOnly,
  preview = false,
  outline = [],
  renderedHtml,
}: {
  initial: string;
  document?: EditorDocument | null;
  onChange: (body: string, document: EditorDocument) => void;
  onBusy: (busy: boolean) => void;
  readOnly: boolean;
  preview?: boolean;
  outline?: { title: string; level: number; anchor: string }[];
  renderedHtml?: string;
}) {
  const [uploads, setUploads] = useState(0);
  const [aiBusy, setAiBusy] = useState(false);
  const uploadActivity = useCallback(
    (delta: number) => setUploads((count) => count + delta),
    [],
  );
  const [state] = useState(() => {
    const headingAnchors = new Map<string, string>();
    const links = new Map<string, Record<string, string>>();
    if (preview && renderedHtml) {
      const rendered = new DOMParser().parseFromString(
        renderedHtml,
        "text/html",
      );
      rendered
        .querySelectorAll<HTMLAnchorElement>("a[data-wiki-href]")
        .forEach((link) => {
          const attributes: Record<string, string> = {};
          for (const key of ["href", "title", "target", "rel", "aria-disabled"])
            if (link.hasAttribute(key))
              attributes[key] = link.getAttribute(key)!;
          links.set(link.getAttribute("data-wiki-href")!, attributes);
        });
    }
    function ReaderHeading(props: PlateElementProps) {
      return (
        <HeadingElement
          {...props}
          variant={props.element.type as "h1"}
          attributes={{
            ...props.attributes,
            id: headingAnchors.get(String(props.element.id)),
          }}
        />
      );
    }
    function ReaderLink(props: PlateElementProps<TLinkElement>) {
      return (
        <LinkElement
          {...props}
          attributes={{ ...props.attributes, ...links.get(props.element.url) }}
        />
      );
    }
    const editor = createPlateEditor({
      override: {
        components: preview
          ? {
              h1: ReaderHeading,
              h2: ReaderHeading,
              h3: ReaderHeading,
              h4: ReaderHeading,
              h5: ReaderHeading,
              h6: ReaderHeading,
              a: ReaderLink,
            }
          : {},
      },
      plugins: [
        ...EditorKit.filter(
          (plugin) =>
            !preview ||
            ![
              "fixed-toolbar",
              "floating-toolbar",
              "aiChat",
              "blockMenu",
              "discussion",
            ].includes(plugin.key),
        ),
        ...(preview
          ? [
              discussionPlugin.configure({
                render: {
                  aboveNodes:
                    () =>
                    ({ children }) => <>{children}</>,
                },
              }),
            ]
          : []),
        RawPlugin,
      ],
    });
    editor.setOption(MarkdownPlugin, "rules", {
      ...editor.getOption(MarkdownPlugin, "rules"),
      raw_markdown: {
        serialize: (node) => ({ type: "html", value: String(node.raw) }),
      },
      placeholder: { serialize: () => ({ type: "html", value: "" }) },
      aiChat: { serialize: () => ({ type: "html", value: "" }) },
    });
    const source = preservedMarkdown(editor, initial, document?.value);
    editor.tf.setValue(source.value);
    const headings = Array.from(
      editor.api.nodes({
        at: [],
        match: (node) =>
          typeof node.type === "string" &&
          ["h1", "h2", "h3", "h4", "h5", "h6"].includes(node.type),
      }),
    );
    let cursor = 0;
    for (const heading of outline) {
      let label = heading.title;
      try {
        label = editor
          .getApi(MarkdownPlugin)
          .markdown.deserialize(label)
          .map((node) => NodeApi.string(node))
          .join("");
      } catch {
        /* Keep the source label if an unsupported extension cannot be parsed. */
      }
      const index = headings.findIndex(
        ([node], i) =>
          i >= cursor &&
          node.type === `h${heading.level}` &&
          NodeApi.string(node) === label,
      );
      if (index >= 0) {
        headingAnchors.set(String(headings[index][0].id), heading.anchor);
        cursor = index + 1;
      }
    }
    editor.setOption(
      discussionPlugin,
      "discussions",
      document?.discussions ?? [],
    );
    return { editor, source, lastValue: valueSnapshot(editor.children) };
  });
  useEffect(() => {
    onBusy(uploads > 0 || aiBusy);
  }, [uploads, aiBusy, onBusy]);
  return (
    <div className="plate-scope wiki-plate">
      <TooltipProvider>
        <UploadActivity.Provider value={uploadActivity}>
          <Plate
            editor={state.editor}
            readOnly={readOnly}
            onValueChange={({ value }) => {
              const snapshot = valueSnapshot(value);
              if (snapshot === state.lastValue) return;
              state.lastValue = snapshot;
              onChange(state.source.serialize(value), {
                version: 1,
                value,
                discussions: state.editor.getOption(
                  discussionPlugin,
                  "discussions",
                ),
              });
            }}
          >
            <EditorContainer variant="default">
              <Editor
                aria-label="本文ブロック"
                placeholder="本文を書く…"
                variant="none"
                className="min-h-72 px-4 py-5"
              />
            </EditorContainer>
            {!preview && (
              <DocumentChanges
                source={state.source}
                onChange={onChange}
                onBusy={setAiBusy}
              />
            )}
          </Plate>
        </UploadActivity.Provider>
        <Toaster position="bottom-right" />
      </TooltipProvider>
    </div>
  );
}
