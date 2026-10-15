import { readdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import type { Store } from "./store";

/** Recover uploads interrupted before DB registration; retain recent/in-flight files. */
export async function cleanOrphanUploads(store: Store, sourceRoot: string, now = Date.now()) {
  const isId = (value: string) => /^[a-f0-9-]{36}$/.test(value);
  for (const source of await readdir(sourceRoot)) {
    if (!isId(source)) continue;
    const sourceDir = join(sourceRoot, source);
    if (!(await stat(sourceDir)).isDirectory()) continue;
    for (const entry of await readdir(sourceDir)) {
      const folder = join(sourceDir, entry);
      if (isId(entry)) {
        if (!store.revision(entry) && (await stat(folder)).mtimeMs < now - 3600000)
          await rm(folder, { recursive: true, force: true });
      } else if (entry === "attachments" && (await stat(folder)).isDirectory()) {
        for (const attachment of await readdir(folder)) {
          if (!isId(attachment)) continue;
          const upload = join(folder, attachment);
          if (
            !store.db
              .query("SELECT 1 FROM attachments WHERE id=? AND source_id=?")
              .get(attachment, source) &&
            (await stat(upload)).mtimeMs < now - 3600000
          )
            await rm(upload, { recursive: true, force: true });
        }
      }
    }
  }
}
