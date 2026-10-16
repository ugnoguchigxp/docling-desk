import { NodeApi, type SlateEditor, type Value } from "platejs";
import { MarkdownPlugin } from "@platejs/markdown";
import { rawParts } from "./raw-markdown";
import { valueSnapshot } from "./plate";

type Group = { ids: string[]; snapshot: string; raw: string; before: string };

/** Preserve untouched source ranges while the complete Plate document is edited. */
export function preservedMarkdown(
  editor: SlateEditor,
  source: string,
  saved?: Value,
) {
  const groups: Group[] = [];
  const byId = new Map<string, number>();
  const parts = rawParts(source);
  const definitions = parts
    .filter((part) => part.kind === "definition")
    .map((part) => part.raw)
    .join("\n\n");
  let spacing = "";
  const value: Value = [];
  for (const part of parts) {
    if (part.kind === "spacing") {
      spacing += part.raw;
      continue;
    }
    let nodes: Value;
    // Unsupported extensions stay visible and inert, rather than being dropped on edit.
    if (
      [
        "yaml",
        "toml",
        "definition",
        "containerDirective",
        "leafDirective",
      ].includes(part.kind)
    ) {
      nodes = [
        { type: "raw_markdown", raw: part.raw, children: [{ text: "" }] },
      ];
    } else {
      try {
        nodes = editor
          .getApi(MarkdownPlugin)
          .markdown.deserialize(
            part.raw + (definitions ? "\n\n" + definitions : ""),
          );
        if (!nodes.length) throw new Error("unsupported_markdown");
        if (
          part.kind === "html" &&
          nodes
            .map((node) => NodeApi.string(node))
            .join("\n")
            .trim() === part.raw.trim()
        )
          throw new Error("unsupported_html");
      } catch {
        nodes = [
          { type: "raw_markdown", raw: part.raw, children: [{ text: "" }] },
        ];
      }
    }
    for (const node of nodes) {
      node.id = crypto.randomUUID();
      byId.set(node.id as string, groups.length);
    }
    groups.push({
      ids: nodes.map((n) => n.id as string),
      snapshot: valueSnapshot(nodes),
      raw: part.raw,
      before: spacing,
    });
    spacing = "";
    value.push(...nodes);
  }
  const original =
    saved ?? (value.length ? value : [{ type: "p", children: [{ text: "" }] }]);
  const snapshot = valueSnapshot(original);
  return {
    value: original,
    serialize(current: Value) {
      if (valueSnapshot(current) === snapshot) return source;
      const chunks: { raw: string; group?: number }[] = [];
      for (let index = 0; index < current.length;) {
        const node = current[index];
        const groupIndex = byId.get(node.id as string);
        const group = groupIndex === undefined ? undefined : groups[groupIndex];
        const nodes = [node];
        index++;
        if (group)
          while (
            index < current.length &&
            byId.get(current[index].id as string) === groupIndex
          )
            nodes.push(current[index++]);
        const unchanged =
          group &&
          valueSnapshot(nodes) === group.snapshot &&
          nodes.every((n, i) => n.id === group.ids[i]) &&
          nodes.length === group.ids.length;
        const raw = unchanged
          ? group.raw
          : node.type === "raw_markdown"
            ? String(node.raw)
            : editor
                .getApi(MarkdownPlugin)
                .markdown.serialize({ value: nodes })
                .replace(/\n$/, "");
        // A trailing empty paragraph is editor chrome until the user types in it.
        if (!raw) continue;
        chunks.push({ raw, group: groupIndex });
      }
      const newline = source.includes("\r\n") ? "\r\n" : "\n";
      let result =
        source.startsWith("\ufeff") && chunks[0]?.group !== 0 ? "\ufeff" : "";
      chunks.forEach((chunk, i) => {
        const previous = chunks[i - 1];
        const unchangedBoundary =
          chunk.group !== undefined &&
          (i === 0
            ? chunk.group === 0
            : previous.group !== undefined &&
              previous.group + 1 === chunk.group);
        result += unchangedBoundary
          ? groups[chunk.group!].before
          : i
            ? newline + newline
            : "";
        result += source.includes("\r\n")
          ? chunk.raw.replace(/\r?\n/g, "\r\n")
          : chunk.raw;
      });
      if (chunks.at(-1)?.group === groups.length - 1) result += spacing;
      return result;
    },
  };
}
