/** Isolated accepted-attempt fixtures. HTTP auth, DB publishing and rendering are real. */
import { copyFile, cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createApi } from "../../knowledge-api/src/app";
import { loadConfig } from "../../knowledge-api/src/config";
import { HttpExtractor } from "../../knowledge-api/src/providers";
import { extractionSchema } from "../../knowledge-api/src/contracts";
import {
  Store,
  type JobRow,
  type RevisionRow,
} from "../../knowledge-api/src/store";
const config = loadConfig();
class Fixtures extends HttpExtractor {
  async ready() {
    return true;
  }
  async extract(job: JobRow, rev: RevisionRow, signal: AbortSignal) {
    // Real TXT conversion also exercises worker -> accepted attempt publication.
    if (rev.suffix === ".txt") return super.extract(job, rev, signal);
    const kind = {
      ".pdf": "page",
      ".pptx": "slide",
      ".xlsx": "sheet",
      ".docx": "docx",
      ".md": "md",
    }[rev.suffix];
    if (!kind) throw new Error("unknown fixture");
    const from = join(process.cwd(), ".cache/viewer-embedding/fixtures", kind);
    const folder = join(
      config.artifactRoot,
      "sources",
      job.source_id!,
      rev.id,
      "attempts",
      `${job.id}-${job.run}`,
    );
    await cp(from, folder, { recursive: true });
    const metadata = JSON.parse(
      await readFile(join(folder, "job.json"), "utf8"),
    );
    metadata.id = job.id;
    metadata.filename = rev.filename;
    await writeFile(join(folder, "job.json"), JSON.stringify(metadata));
    await copyFile(
      join(
        config.artifactRoot,
        "sources",
        job.source_id!,
        rev.id,
        `original${rev.suffix}`,
      ),
      join(folder, `original${rev.suffix}`),
    );
    const records = async (name: string) =>
      (await readFile(join(folder, name), "utf8"))
        .split("\n")
        .filter(Boolean)
        .map((line) => ({ ...JSON.parse(line), source_sha256: rev.sha256 }));
    return extractionSchema.parse({
      source_id: job.source_id,
      source_revision: rev.id,
      job_id: job.id,
      run: job.run,
      source_sha256: rev.sha256,
      profile: JSON.parse(job.payload).profile,
      contexts: await records("rag.jsonl"),
      chunks: await records("rag-index.jsonl"),
      warnings: [],
      viewer_ready: true,
    });
  }
}
await mkdir(config.root, { recursive: true });
const store = new Store(join(config.root, "knowledge.sqlite"));
const api = createApi({
  ...config,
  store,
  extractor: new Fixtures(
    process.env.KNOWLEDGE_WORKER_URL!,
    process.env.KNOWLEDGE_WORKER_TOKEN!,
  ),
});
const server = Bun.serve({
  hostname: "0.0.0.0",
  port: 18866,
  fetch: api.app.fetch,
  idleTimeout: 60,
});
setInterval(() => void api.worker.tick(), 100);
console.log(`Isolated fixture API ${server.port}`);
