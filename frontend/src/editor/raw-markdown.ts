import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkFrontmatter from "remark-frontmatter";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import remarkDirective from "remark-directive";
import type { RootContent } from "mdast";

export type RawPart = {
  raw: string;
  editable: boolean;
  kind: string;
  images?: { attachment_id: string; alt: string }[];
};
const parser = unified()
  .use(remarkParse)
  .use(remarkFrontmatter, ["yaml", "toml"])
  .use(remarkMath)
  .use(remarkGfm)
  .use(remarkDirective);
const supported = new Set([
  "paragraph",
  "heading",
  "list",
  "listItem",
  "text",
  "strong",
  "emphasis",
  "inlineCode",
  "link",
  "break",
]);
function editable(node: RootContent): boolean {
  if (!supported.has(node.type)) return false;
  if (node.type === "link" && !/^(https?:|mailto:|#)/i.test(node.url))
    return false;
  return (
    !("children" in node) ||
    node.children.every((child) => editable(child as RootContent))
  );
}
function images(node: RootContent): { attachment_id: string; alt: string }[] {
  if (
    node.type === "image" &&
    /^knowledge-attachment:[0-9a-f-]{36}$/.test(node.url)
  )
    return [
      {
        attachment_id: node.url.slice("knowledge-attachment:".length),
        alt: node.alt ?? "",
      },
    ];
  return "children" in node
    ? node.children.flatMap((child) => images(child as RootContent))
    : [];
}
/** Slice the original, never serialize the parsed tree back over the document. */
export function rawParts(source: string): RawPart[] {
  const prefix = source.startsWith("\ufeff") ? 1 : 0;
  const tree = parser.parse(source.slice(prefix));
  const parts: RawPart[] = [];
  let cursor = 0;
  for (const node of tree.children) {
    const start =
      node.position?.start.offset === undefined
        ? undefined
        : node.position.start.offset + prefix;
    const end =
      node.position?.end.offset === undefined
        ? undefined
        : node.position.end.offset + prefix;
    if (start === undefined || end === undefined)
      throw new Error("source_position_unavailable");
    if (cursor < start)
      parts.push({
        raw: source.slice(cursor, start),
        editable: false,
        kind: "spacing",
      });
    parts.push({
      raw: source.slice(start, end),
      editable: editable(node),
      kind: node.type,
      images: images(node),
    });
    cursor = end;
  }
  if (cursor < source.length)
    parts.push({ raw: source.slice(cursor), editable: false, kind: "spacing" });
  return parts;
}

export function isWikiImage(url: string): boolean {
  return /^\/api\/wiki\/images\/[a-f0-9]{64}\.(png|jpg|gif|webp)$/.test(url);
}

export function firstPlateImage(
  nodes: import("platejs").Value,
): ReturnType<typeof firstMarkdownImage> {
  for (const node of nodes) {
    if (node.type === "img" && typeof node.url === "string") {
      const caption = Array.isArray(node.caption)
        ? node.caption.map((c) => c.text).join("")
        : "";
      return {
        url: node.url,
        alt: typeof node.alt === "string" ? node.alt : caption,
        caption: Array.isArray(node.caption)
          ? caption
          : typeof node.title === "string"
            ? node.title
            : "",
      };
    }
    if ("children" in node) {
      const image = firstPlateImage(node.children as import("platejs").Value);
      if (image) return image;
    }
  }
  return null;
}

/** Read document order from the AST; code, frontmatter and HTML are not images. */
export function firstMarkdownImage(source: string): {
  url: string;
  alt: string;
  caption: string;
} | null {
  const tree = parser.parse(source.replace(/^\ufeff/, ""));
  const definitions = new Map(
    tree.children
      .filter((n) => n.type === "definition")
      .map((n) => [n.identifier, n]),
  );
  function visit(node: RootContent): ReturnType<typeof firstMarkdownImage> {
    if (node.type === "image")
      return { url: node.url, alt: node.alt ?? "", caption: node.title ?? "" };
    if (node.type === "imageReference") {
      const definition = definitions.get(node.identifier);
      if (definition)
        return {
          url: definition.url,
          alt: node.alt ?? "",
          caption: definition.title ?? "",
        };
    }
    if ("children" in node)
      for (const child of node.children) {
        const found = visit(child as RootContent);
        if (found) return found;
      }
    return null;
  }
  for (const node of tree.children) {
    const found = visit(node);
    if (found) return found;
  }
  return null;
}
export function combineParts(parts: RawPart[]): string {
  return parts.map((part) => part.raw).join("");
}
export function replacePart(
  parts: RawPart[],
  index: number,
  expected: string,
  replacement: string,
): RawPart[] {
  if (!parts[index]?.editable || parts[index].raw !== expected)
    throw new Error("source_changed");
  return parts.map((part, i) =>
    i === index ? { ...part, raw: replacement } : part,
  );
}
