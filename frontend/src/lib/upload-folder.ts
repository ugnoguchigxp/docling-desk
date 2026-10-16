import { post, request } from "./api";
import type { Folder, Library } from "./types";

// Share only an in-flight lookup. A later upload must notice moves/deletions.
let pending: Promise<string> | undefined;

async function resolveFolder(): Promise<string> {
  const find = (library: Library) =>
    library.folders.find(
      (folder) =>
        folder.parent_id === null && folder.name.toLowerCase() === "upload",
    );
  const existing = find(await request<Library>("/api/library"));
  if (existing) return existing.id;
  try {
    return (
      await post<Folder>("/api/folders", { name: "upload", parent_id: null })
    ).id;
  } catch (error) {
    // Another tab may have created the same folder after our snapshot.
    const created = find(await request<Library>("/api/library"));
    if (created) return created.id;
    throw error;
  }
}

export function uploadFolder(): Promise<string> {
  pending ??= resolveFolder().finally(() => {
    pending = undefined;
  });
  return pending;
}
