import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store } from "../src/store";

const rootDir = join(import.meta.dir, "..");

function run(db: string, fault?: string) {
  return spawnSync(process.execPath, ["scripts/fault-publish-child.ts", db], {
    cwd: rootDir,
    env: { ...process.env, ...(fault ? { DOCLING_FAULT: fault } : {}) },
    encoding: "utf8",
  });
}

describe("publish crash boundary", () => {
  test("a killed publish does not leave searchable evidence", () => {
    const root = mkdtempSync(join(tmpdir(), "fault-publish-"));
    const db = join(root, "db.sqlite");
    try {
      const killed = run(db, "exit:api_publish_before_commit");
      expect(killed.signal).toBe("SIGKILL");
      const store = new Store(db);
      const revision = store.db
        .query<{ evidence_revision: string | null; state: string }, []>(
          "SELECT evidence_revision, state FROM revisions",
        )
        .get();
      const chunks = store.db.query<{ n: number }, []>("SELECT count(*) n FROM chunks").get();
      expect(revision?.evidence_revision ?? null).toBeNull();
      expect(revision?.state).toBe("queued");
      expect(chunks?.n).toBe(0);
      store.db.close();
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  test("the same publish commits when the crash boundary is not armed", () => {
    const root = mkdtempSync(join(tmpdir(), "fault-publish-ok-"));
    const db = join(root, "db.sqlite");
    try {
      const completed = run(db);
      expect(completed.status).toBe(0);
      const store = new Store(db);
      const revision = store.db
        .query<{ evidence_revision: string | null }, []>("SELECT evidence_revision FROM revisions")
        .get();
      const chunks = store.db.query<{ n: number }, []>("SELECT count(*) n FROM chunks").get();
      expect(revision?.evidence_revision).toBeTruthy();
      expect(chunks?.n).toBe(1);
      store.db.close();
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
