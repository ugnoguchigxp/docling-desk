import { expect, it } from "vitest";
import type { Folder, Item, Job, Library } from "../../lib/types";
import { diagramFilename } from "./diagram";
import { filterEntries, listEntries, toItems } from "./entries";
import {
  crumbs,
  foldersHoldingDiagram,
  isWithin,
  movesIntoItself,
} from "./folders";
import { initialName, isNaming, toRefs } from "./operations";

const folders: Folder[] = [
  { id: "a", name: "A", parent_id: null, created: 1 },
  { id: "b", name: "B", parent_id: "a", created: 2 },
  { id: "c", name: "C", parent_id: "b", created: 3 },
  { id: "z", name: "Z", parent_id: null, created: 4 },
  { id: "loop", name: "Loop", parent_id: "loop", created: 5 },
];
function job(partial: Partial<Job> & Pick<Job, "id" | "filename">): Job {
  return {
    folder_id: null,
    state: "success",
    created: 1,
    original_filename: null,
    duration: null,
    pages: 1,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: null,
    slide_layout: false,
    ...partial,
  };
}
const image = {
  width: 1,
  height: 1,
  media_type: "image/png",
} as NonNullable<Job["image"]>;
const jobs = [
  job({
    id: "scene",
    filename: "s.excalidraw",
    folder_id: "c",
    excalidraw: true,
  }),
  job({ id: "img", filename: "p.png", folder_id: "b", image, created: 9 }),
  job({ id: "img2", filename: "q.png", folder_id: null, image, created: 8 }),
  job({ id: "doc", filename: "d.pdf", folder_id: "a", created: 7 }),
];
const data: Library = { folders, jobs };

it("decides whether a folder lies below another, even through a cycle", () => {
  expect(isWithin(folders, "c", "a")).toBe(true);
  expect(isWithin(folders, "a", "a")).toBe(true);
  expect(isWithin(folders, "a", "c")).toBe(false);
  expect(isWithin(folders, null, "a")).toBe(false);
  expect(isWithin(folders, "loop", "a")).toBe(false);
  expect(crumbs(folders, "b").map((c) => c.id)).toEqual([null, "a", "b"]);
});

it("refuses a destination that is a moved folder or below it, never a plain file", () => {
  const items = toItems(data);
  const folderA = items.find((i) => i.kind === "folder" && i.id === "a")!;
  const file = items.find((i) => i.kind === "file" && i.id === "doc")!;
  expect(movesIntoItself(folders, [folderA], "a")).toBe(true);
  expect(movesIntoItself(folders, [folderA], "c")).toBe(true);
  expect(movesIntoItself(folders, [folderA], "z")).toBe(false);
  expect(movesIntoItself(folders, [folderA], null)).toBe(false);
  expect(movesIntoItself(folders, [file], "a")).toBe(false);
  expect(movesIntoItself(folders, [file, folderA], "b")).toBe(true);
});

it("marks every ancestor of a diagram, and only those", () => {
  expect([...foldersHoldingDiagram(data)].sort()).toEqual(["a", "b", "c"]);
  expect(foldersHoldingDiagram({ folders, jobs: [] }).size).toBe(0);
});

it("lists a folder's children or the images below it", () => {
  const items = toItems(data);
  const ids = (list: Item[]) => list.map((i) => `${i.kind}:${i.id}`).sort();
  expect(ids(listEntries(items, folders, "folder", null))).toEqual([
    "file:img2",
    "folder:a",
    "folder:z",
  ]);
  expect(ids(listEntries(items, folders, "folder", "a"))).toEqual([
    "file:doc",
    "folder:b",
  ]);
  expect(ids(listEntries(items, folders, "images", null))).toEqual([
    "file:img",
    "file:img2",
  ]);
  expect(ids(listEntries(items, folders, "images", "a"))).toEqual(["file:img"]);
  expect(ids(listEntries(items, folders, "images", "z"))).toEqual([]);
});

it("filters by text and type, then puts folders first", () => {
  const entries = listEntries(toItems(data), folders, "folder", "a");
  const names = (query: string, type = "", sort = "recent") =>
    filterEntries(entries, { query, type, sort }).map((i) =>
      i.kind === "file" ? i.filename : i.name,
    );
  expect(names("")).toEqual(["B", "d.pdf"]);
  expect(names("  D.PDF ")).toEqual(["d.pdf"]);
  expect(names("", "pdf")).toEqual(["B", "d.pdf"]);
  expect(names("", "png")).toEqual(["B"]);
  const all = toItems(data);
  expect(
    filterEntries(all, { query: "", type: "excalidraw", sort: "name" })
      .filter((i) => i.kind === "file")
      .map((i) => i.id),
  ).toEqual(["scene"]);
});

it("normalizes names and operation helpers", () => {
  expect(diagramFilename("")).toBe("新しい図表.excalidraw");
  expect(diagramFilename(" A ")).toBe("A.excalidraw");
  expect(diagramFilename("B.EXCALIDRAW")).toBe("B.EXCALIDRAW");
  expect(isNaming("create")).toBe(true);
  expect(isNaming("rename")).toBe(true);
  expect(isNaming("move")).toBe(false);
  const [file] = toItems(data).filter((i) => i.kind === "file");
  expect(toRefs([file])).toEqual([{ kind: "file", id: "scene" }]);
  expect(initialName({ action: "rename", items: [file], parent: null })).toBe(
    "s.excalidraw",
  );
  expect(initialName({ action: "move", items: [file], parent: null })).toBe("");
});
