import { createPlateEditor } from "platejs/react";
import type { NodeComponents } from "platejs";
import { MarkdownPlugin } from "@platejs/markdown";
import {
  BasicBlocksPlugin,
  BasicMarksPlugin,
} from "@platejs/basic-nodes/react";
import { ListPlugin } from "@platejs/list/react";
import { LinkPlugin } from "@platejs/link/react";
import remarkGfm from "remark-gfm";

export function markdownEditor(raw: string, components: NodeComponents = {}) {
  return createPlateEditor({
    override: { components },
    plugins: [
      BasicBlocksPlugin,
      BasicMarksPlugin,
      ListPlugin,
      LinkPlugin,
      MarkdownPlugin.configure({ options: { remarkPlugins: [remarkGfm] } }),
    ],
    value: (editor) => editor.getApi(MarkdownPlugin).markdown.deserialize(raw),
  });
}
export function valueSnapshot(value: unknown): string {
  return JSON.stringify(value, (key, item) =>
    key === "id" ? undefined : item,
  );
}
export function editedMarkdown(
  editor: ReturnType<typeof markdownEditor>,
  original: string,
  initial: string,
): string {
  if (valueSnapshot(editor.children) === initial) return original;
  const serialized = editor
    .getApi(MarkdownPlugin)
    .markdown.serialize()
    .replace(/\n$/, "");
  return original.includes("\r\n")
    ? serialized.replace(/\n/g, "\r\n")
    : serialized;
}
