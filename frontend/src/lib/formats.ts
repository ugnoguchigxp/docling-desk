/**
 * Static facts about file names, one table. What a given document can do (view,
 * extract, translate, print) is not decided here: the server reports it in the
 * job (`image`, `diagram`, `excalidraw`, `preview`, `tex_preview`, `state`), and
 * a name says nothing sure about content (a `.png` may carry a draw.io model,
 * an `.html` may hold none). Use `viewKind` and the job fields for behaviour,
 * and these helpers for upload filters, labels and fallbacks.
 *
 * The accepted set mirrors the server's upload check; the shared fixture
 * `tests/fixtures/format-contract/` keeps the two from drifting.
 */
const families = {
  pdf: [".pdf"],
  pptx: [".pptx"],
  xlsx: [".xlsx"],
  docx: [".docx"],
  markdown: [".md", ".markdown"],
  text: [".txt", ".text"],
  tex: [".tex", ".latex"],
  image: [".png", ".jpg", ".jpeg", ".gif", ".webp"],
  drawio: [".drawio", ".dio", ".xml", ".svg", ".html", ".htm"],
  excalidraw: [".excalidraw"],
} as const;
export type FormatFamily = keyof typeof families;

// The server turns an uploaded PNG that carries a draw.io model into a diagram.
const DRAWIO_PNG = ".drawio.png";

const extensionsOf = (...names: FormatFamily[]): string[] =>
  names.flatMap((name) => families[name]);
// A bare ".pdf" is a hidden file without an extension; the server's suffix
// check (pathlib) refuses it, so a name needs something before the dot.
const endsWithAny = (name: string, extensions: readonly string[]) => {
  const lower = name.toLowerCase();
  return extensions.some(
    (extension) => lower.endsWith(extension) && lower.length > extension.length,
  );
};
const is =
  (...names: FormatFamily[]) =>
  (name: string) =>
    endsWithAny(name, extensionsOf(...names));

/** File-chooser `accept` list and the label shown next to it. */
export const supportedAccept = extensionsOf(
  ...(Object.keys(families) as FormatFamily[]),
).join(",");
export const supportedLabel =
  "PDF / PPTX / XLSX / DOCX / Markdown / テキスト / TeX / 画像 / draw.io / Excalidraw";

export const isPdf = is("pdf");
export const isPptx = is("pptx");
export const isXlsx = is("xlsx");
export const isDocx = is("docx");
export const isTex = is("tex");
export const isExcalidraw = is("excalidraw");
/** Documents the server reads as one whole unit rather than pages. */
export const isDocument = is("docx", "markdown", "text", "tex");
/** Plain text sources, shown as typed rather than rendered. */
export const isSource = is("text", "tex");
export const isSupported = is(
  "pdf",
  "pptx",
  "xlsx",
  "docx",
  "markdown",
  "text",
  "tex",
  "image",
  "drawio",
  "excalidraw",
);

/** Name of the file the person uploaded (the stored `filename` may differ). */
export const originalName = (job: {
  filename: string;
  original_filename?: string | null;
}) => job.original_filename || job.filename;

/**
 * The family the server files a name under at upload: draw.io before image
 * (a `.drawio.png` is a diagram), Excalidraw first. null means rejected.
 */
export function familyOf(name: string): FormatFamily | null {
  if (isExcalidraw(name)) return "excalidraw";
  if (name.toLowerCase().endsWith(DRAWIO_PNG) || is("drawio")(name))
    return "drawio";
  const order: FormatFamily[] = [
    "pdf",
    "pptx",
    "xlsx",
    "docx",
    "markdown",
    "text",
    "tex",
    "image",
  ];
  return order.find((family) => is(family)(name)) ?? null;
}

/** The value of the library's file-type filter that a name belongs to. */
export function matchesFormat(name: string, format: string) {
  if (format === "drawio")
    return (
      endsWithAny(name, families.drawio) ||
      name.toLowerCase().endsWith(DRAWIO_PNG)
    );
  if (format === "image") return is("image")(name);
  if (format === "jpg") return endsWithAny(name, [".jpg", ".jpeg"]);
  if (format === "md") return is("markdown")(name);
  if (format === "tex") return isTex(name);
  if (format === "txt") return is("text")(name);
  return name.toLowerCase().endsWith("." + format);
}

/** Which viewer a stored document is opened in; the server's flags decide. */
export type ViewKind = "excalidraw" | "drawio" | "image" | "extracted";
export function viewKind(job: {
  excalidraw?: boolean;
  diagram?: boolean;
  image?: unknown;
}): ViewKind {
  if (job.excalidraw) return "excalidraw";
  if (job.diagram) return "drawio";
  if (job.image) return "image";
  return "extracted";
}

/** Wiki import: Markdown articles and CSV tables of contents, plus a manifest. */
const wikiArticle = [".md", ".markdown", ".csv"] as const;
const wikiManifest = ["wiki-manifest.json", "pages.jsonl"] as const;
export const isWikiArticle = (name: string) => endsWithAny(name, wikiArticle);
export const isWikiImportFile = (name: string) =>
  isWikiArticle(name) || (wikiManifest as readonly string[]).includes(name);
