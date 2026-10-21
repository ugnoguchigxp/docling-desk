import { matchesFormat, isExcalidraw, originalName } from "../../lib/formats";
import {
  itemName,
  type Folder,
  type Item,
  type Job,
  type Library as Snapshot,
} from "../../lib/types";
import { chain } from "./folders";

export type Scope = "folder" | "images";
export type Layout = "list" | "gallery";
export type Sort = "recent" | "oldest" | "name";
export interface Filter {
  query: string;
  type: string;
  sort: Sort | string;
}
export const FILE_TYPES: [value: string, label: string][] = [
  ["pdf", "PDF"],
  ["pptx", "PowerPoint"],
  ["xlsx", "Excel"],
  ["docx", "Word"],
  ["md", "Markdown"],
  ["txt", "テキスト"],
  ["tex", "TeX"],
  ["drawio", "draw.io"],
  ["excalidraw", "Excalidraw"],
  ["image", "画像"],
  ["png", "PNG"],
  ["jpg", "JPEG"],
  ["gif", "GIF"],
  ["webp", "WebP"],
];
export const SORTS: [value: Sort, label: string][] = [
  ["recent", "追加日時：新しい順"],
  ["oldest", "追加日時：古い順"],
  ["name", "ファイル名順"],
];
/** Folders and files as one list of tagged items. */
export function toItems(data: Snapshot): Item[] {
  return [
    ...data.folders.map((f: Folder) => ({ ...f, kind: "folder" as const })),
    ...data.jobs.map((j: Job) => ({ ...j, kind: "file" as const })),
  ];
}
/** Items shown for the route: the folder's children, or every image below it. */
export function listEntries(
  items: Item[],
  folders: Folder[],
  scope: Scope,
  folder: string | null,
) {
  return items.filter((i) =>
    scope === "images"
      ? i.kind === "file" &&
        i.image &&
        (!folder ||
          i.folder_id === folder ||
          chain(folders, i.folder_id).some((f) => f.id === folder))
      : (i.kind === "folder" ? i.parent_id : i.folder_id) === folder,
  );
}
/** Narrow by search text and type, then order folders first. */
export function filterEntries(entries: Item[], { query, type, sort }: Filter) {
  const needle = query.trim().normalize("NFC").toLocaleLowerCase("ja");
  return entries
    .filter(
      (i) =>
        [
          itemName(i),
          ...(i.kind === "file" && i.image
            ? [
                i.original_filename || "",
                i.image_metadata?.description || "",
                ...(i.image_metadata?.tags || []),
              ]
            : []),
        ]
          .join(" ")
          .normalize("NFC")
          .toLocaleLowerCase("ja")
          .includes(needle) &&
        (!type ||
          i.kind === "folder" ||
          // What the server says about the content wins over the name.
          (type === "drawio"
            ? !!i.diagram
            : type === "excalidraw"
              ? !!i.excalidraw || isExcalidraw(originalName(i))
              : matchesFormat(originalName(i), type))),
    )
    .sort(
      (a, b) =>
        (a.kind === b.kind ? 0 : a.kind === "folder" ? -1 : 1) ||
        (sort === "name"
          ? itemName(a).localeCompare(itemName(b), "ja")
          : sort === "oldest"
            ? a.created - b.created
            : b.created - a.created) ||
        a.id.localeCompare(b.id),
    );
}
/** Extension shown on file badges; Excalidraw files get a short Japanese label. */
export function kindLabel(job: Pick<Job, "excalidraw">, name: string) {
  return job.excalidraw || isExcalidraw(name)
    ? "図表"
    : name.split(".").pop()?.toUpperCase();
}
