import { expect, test } from "bun:test";
import { chmodSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

test("credential synchronization preserves OCR and quota configuration", () => {
  const root = mkdtempSync(resolve(tmpdir(), "knowledge-config-"));
  const cwd = resolve(import.meta.dir, "..");
  const run = (script: string) => {
    const result = Bun.spawnSync([process.execPath, script], {
      cwd,
      env: { ...process.env, KNOWLEDGE_STATE_DIR: root },
      stdout: "pipe",
      stderr: "pipe",
    });
    expect(result.exitCode).toBe(0);
    expect(result.stdout.toString()).not.toContain("synthetic-ocr-secret");
    expect(result.stderr.toString()).not.toContain("synthetic-ocr-secret");
  };
  try {
    run("scripts/setup-local.ts");
    const token = readFileSync(`${root}/worker.env`, "utf8").match(
      /KNOWLEDGE_WORKER_TOKEN=(\w+)/,
    )?.[1];
    const ocr =
      '\n# Keep VM OCR settings\nDOCLING_AZURE_OCR_ENDPOINT=https://vm.cognitiveservices.azure.com\nAZURE_DOCUMENT_INTELLIGENCE_API_KEY="synthetic-ocr-secret # with spaces"\n';
    writeFileSync(`${root}/worker.env`, `KNOWLEDGE_WORKER_TOKEN=stale\n${ocr}`);
    writeFileSync(
      `${root}/container.env`,
      "KNOWLEDGE_WORKER_TOKEN=stale\nKNOWLEDGE_CLIENTS_B64=stale\nKNOWLEDGE_OCR_MONTHLY_SUBMISSIONS=50\n",
    );
    chmodSync(`${root}/worker.env`, 0o644);
    for (let i = 0; i < 2; i++) {
      run("scripts/sync-container-config.ts");
      const worker = readFileSync(`${root}/worker.env`, "utf8");
      const api = readFileSync(`${root}/container.env`, "utf8");
      expect(worker).toContain(ocr.trimStart());
      expect(worker.match(/^KNOWLEDGE_WORKER_TOKEN=/gm)).toHaveLength(1);
      expect(worker).toContain(`KNOWLEDGE_WORKER_TOKEN=${token}`);
      expect(api).toContain("KNOWLEDGE_OCR_MONTHLY_SUBMISSIONS=50");
      expect(api).not.toContain("=stale");
      expect(api).not.toContain("synthetic-ocr-secret");
      expect(statSync(`${root}/worker.env`).mode & 0o777).toBe(0o600);
      expect(statSync(`${root}/container.env`).mode & 0o777).toBe(0o600);
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
