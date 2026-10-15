import { expect, test } from "bun:test";
import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readdir, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cleanOrphanUploads } from "../src/artifact-maintenance";
import { Store } from "../src/store";

test("restart cleanup removes only old unregistered uploads, including attachments", async () => {
  const root = await mkdtemp(join(tmpdir(), "orphan-editing-"));
  const store = new Store(join(root, "db.sqlite"));
  try {
    const source = randomUUID(),
      registered = randomUUID(),
      orphan = randomUUID(),
      recent = randomUUID();
    const folder = join(root, "sources", source, "attachments");
    const old = new Date(Date.now() - 7200000);
    for (const id of [registered, orphan, recent]) {
      await mkdir(join(folder, id), { recursive: true });
      await writeFile(join(folder, id, "original"), "immutable");
      if (id !== recent) await utimes(join(folder, id), old, old);
    }
    store.db.run("INSERT INTO attachments VALUES(?,?,?,?,?,?,?)", [
      registered,
      source,
      randomUUID(),
      "x",
      "image/png",
      9,
      "hash",
    ]);
    await cleanOrphanUploads(store, join(root, "sources"));
    expect((await readdir(folder)).sort()).toEqual([registered, recent].sort());
  } finally {
    store.db.close();
    await rm(root, { recursive: true, force: true });
  }
});
