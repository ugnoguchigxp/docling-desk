import type { Folder, Item, Library as Snapshot } from "../../lib/types";

/** Ancestors of `id` (root first, `id` last). Missing ids and cycles end the walk. */
export function chain(folders: Folder[], id: string | null) {
  const result: Folder[] = [],
    seen = new Set<string>();
  while (id && !seen.has(id)) {
    seen.add(id);
    const f = folders.find((f) => f.id === id);
    if (!f) break;
    result.unshift(f);
    id = f.parent_id;
  }
  return result;
}
/** Display path such as "資料一覧 / A / B". Shown in dialogs and the upload destination. */
export function folderPath(folders: Folder[], id: string | null) {
  return ["資料一覧", ...chain(folders, id).map((f) => f.name)].join(" / ");
}
/** Breadcrumb items for a folder, starting with the library root. */
export function crumbs(folders: Folder[], id: string | null) {
  return [{ id: null, name: "資料一覧" }, ...chain(folders, id)];
}
/** True when `id` is `ancestor` itself or lies below it. */
export function isWithin(
  folders: Folder[],
  id: string | null,
  ancestor: string,
) {
  return id === ancestor || chain(folders, id).some((p) => p.id === ancestor);
}
/**
 * A folder among `items` cannot receive itself or anything below it
 * (the server checks again; this keeps the choice from being offered).
 */
export function movesIntoItself(
  folders: Folder[],
  items: Item[],
  destination: string | null,
) {
  return items.some(
    (i) => i.kind === "folder" && isWithin(folders, destination, i.id),
  );
}
/** Folders that directly or indirectly contain a diagram (copy is refused). */
export function foldersHoldingDiagram(data: Snapshot) {
  const ids = new Set<string>();
  for (const j of data.jobs)
    if (j.excalidraw)
      for (const f of chain(data.folders, j.folder_id)) ids.add(f.id);
  return ids;
}
