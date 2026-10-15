import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store } from "../src/store";

const apiRoot = join(import.meta.dir, "..");
function run(root: string, fault?: string, mode?: string) {
  return spawnSync(process.execPath, ["scripts/fault-editing-child.ts", root, mode ?? "source"], {
    cwd: apiRoot,
    env: { ...process.env, DOCLING_FAULT: fault ?? "", DOCLING_QUALITY_FAULT: "" },
    encoding: "utf8",
  });
}
describe("editing process crash and lost response", () => {
  for (const checkpoint of ["original_written", "register_before_commit", "editing_saved"]) {
    test(checkpoint, () => {
      const root = mkdtempSync(join(tmpdir(), "editing-crash-"));
      try {
        expect(run(root, "exit:" + checkpoint).signal).toBe("SIGKILL");
        const after = new Store(join(root, "knowledge.sqlite"));
        expect(after.db.query("SELECT count(*) AS n FROM sources").get()).toEqual({
          n: checkpoint === "editing_saved" ? 1 : 0,
        });
        after.db.close();
        const retry = run(root);
        expect(retry.status).toBe(0);
        const saved = JSON.parse(retry.stdout);
        const reopened = new Store(join(root, "knowledge.sqlite"));
        expect(reopened.db.query("SELECT count(*) AS n FROM sources").get()).toEqual({ n: 1 });
        expect(reopened.db.query("SELECT count(*) AS n FROM revisions").get()).toEqual({ n: 1 });
        expect(reopened.db.query("SELECT count(*) AS n FROM jobs").get()).toEqual({ n: 1 });
        const bytes = readFileSync(
          join(root, "sources", saved.source_id, saved.source_revision, "original.md"),
        );
        expect(Bun.CryptoHasher.hash("sha256", bytes, "hex")).toBe(saved.sha256);
        expect(bytes.subarray(0, 3).toString("hex")).toBe("efbbbf");
        reopened.db.close();
      } finally {
        rmSync(root, { recursive: true, force: true });
      }
    });
  }
});

for (const checkpoint of ["attachment_written", "attachment_before_commit", "attachment_saved"]) {
  test(`attachment crash: ${checkpoint}`, () => {
    const root = mkdtempSync(join(tmpdir(), "attachment-crash-"));
    try {
      expect(run(root, "exit:" + checkpoint, "attachment").signal).toBe("SIGKILL");
      const retry = run(root, undefined, "attachment");
      expect(retry.status).toBe(0);
      const saved = JSON.parse(retry.stdout);
      const reopened = new Store(join(root, "knowledge.sqlite"));
      expect(reopened.db.query("SELECT count(*) n FROM attachments").get()).toEqual({ n: 1 });
      expect(reopened.db.query("SELECT count(*) n FROM drafts").get()).toEqual({ n: 1 });
      const draft = reopened.db
        .query<{ version: number; attachments: string }, []>(
          "SELECT version,attachments FROM drafts",
        )
        .get();
      expect(draft?.version).toBe(2);
      expect(JSON.parse(draft?.attachments ?? "[]")).toEqual([saved.attachment_id]);
      const bytes = readFileSync(
        join(root, "sources", saved.source_id, "attachments", saved.attachment_id, "original"),
      );
      expect(Bun.CryptoHasher.hash("sha256", bytes, "hex")).toBe(saved.sha256);
      reopened.db.close();
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}
