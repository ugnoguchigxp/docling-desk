import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store } from "../src/store";

function child(path: string, kill = false) {
  return spawnSync(
    process.execPath,
    [
      "-e",
      `import { Store } from './src/store';
       try {
         const store = new Store(${JSON.stringify(path)});
         store.db.run("INSERT INTO api_lease VALUES(1,'child',0)");
         ${kill ? "process.kill(process.pid, 'SIGKILL');" : "store.db.close();"}
       } catch (error) {
         console.error(String(error));
         process.exit(42);
       }`,
    ],
    { cwd: join(import.meta.dir, ".."), encoding: "utf8", timeout: 10000 },
  );
}

test("process ownership survives an expired lease and one handle closing", () => {
  const root = mkdtempSync(join(tmpdir(), "knowledge-writer-"));
  const path = join(root, "knowledge.sqlite");
  const first = new Store(path);
  const second = new Store(path);
  try {
    first.acquireLease();
    first.db.run("UPDATE api_lease SET expires=0");
    first.db.close();
    const denied = child(path);
    expect(denied.status).toBe(42);
    expect(denied.stderr).toContain("already has a writer process");
    expect(second.db.query("SELECT owner FROM api_lease").get()).not.toEqual({ owner: "child" });
    second.db.run("DELETE FROM api_lease");
    second.db.close();
    expect(child(path).status).toBe(0);
  } finally {
    first.db.close();
    second.db.close();
    rmSync(root, { recursive: true, force: true });
  }
});

test("a killed process releases ownership and its committed data survives", () => {
  const root = mkdtempSync(join(tmpdir(), "knowledge-writer-crash-"));
  const path = join(root, "knowledge.sqlite");
  try {
    expect(child(path, true).signal).toBe("SIGKILL");
    const reopened = new Store(path);
    try {
      expect(reopened.db.query("SELECT owner FROM api_lease").get()).toEqual({ owner: "child" });
      expect(reopened.db.query("PRAGMA integrity_check").get()).toEqual({ integrity_check: "ok" });
    } finally {
      reopened.db.close();
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
