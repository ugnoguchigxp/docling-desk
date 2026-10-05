import { randomBytes } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createApi } from "../src/app";
import { clientSchema } from "../src/auth";
import { HttpExtractor } from "../src/providers";
import {
  contextResponse,
  errorResponse,
  registrationResponse,
  searchResponse,
  sourceResponse,
} from "../src/responses";
import { Store } from "../src/store";

// Local integration only: real Python/Docling, temporary data, no embedding/answer/cloud calls.
const root = await mkdtemp(join(tmpdir(), "knowledge-http-smoke-"));
const repo = resolve(import.meta.dir, "../..");
const reservation = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  fetch: () => new Response("reserved"),
});
const port = reservation.port;
await reservation.stop(true);
const token = randomBytes(32).toString("hex");
const processor = Bun.spawn(
  [
    process.env.KNOWLEDGE_PYTHON ?? `${repo}/.venv/bin/python`,
    "-m",
    "uvicorn",
    "knowledge_worker:app_factory",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--no-access-log",
  ],
  {
    cwd: repo,
    env: {
      ...process.env,
      KNOWLEDGE_ARTIFACT_ROOT: root,
      KNOWLEDGE_WORKER_TOKEN: token,
      KNOWLEDGE_API_URL: "",
      DOCLING_AZURE_OCR_ENDPOINT: "",
      AZURE_DOCUMENT_INTELLIGENCE_API_KEY: "",
    },
    stdout: "ignore",
    stderr: "pipe",
  },
);
const store = new Store(join(root, "knowledge.sqlite"));
const client = clientSchema.parse({
  id: "smoke",
  issuer: "smoke",
  token: randomBytes(32).toString("hex"),
  keys: {},
  scopes: [{ collection_id: "wiki" }],
  mode: "batch",
  actions: ["read", "write"],
});
const api = createApi({
  store,
  artifactRoot: root,
  clients: () => [client],
  extractor: new HttpExtractor(`http://127.0.0.1:${port}`, token),
});
const server = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: api.app.fetch });
const url = `http://127.0.0.1:${server.port}`;
async function request(
  path: string,
  method = "GET",
  body?: unknown,
  extra: Record<string, string> = {},
) {
  const response = await fetch(`${url}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${client.token}`,
      ...(body ? { "content-type": "application/json" } : {}),
      ...extra,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok)
    throw new Error(`HTTP integration failed: ${response.status} ${await response.text()}`);
  return response;
}
try {
  let online = false;
  for (let i = 0; i < 160; i++) {
    if (processor.exitCode !== null) throw new Error("Python processor failed to start");
    try {
      online = (
        await fetch(`http://127.0.0.1:${port}/health/live`, { signal: AbortSignal.timeout(1000) })
      ).ok;
    } catch {}
    if (online) break;
    await Bun.sleep(250);
  }
  if (!online) throw new Error("Python processor did not start in time");
  await writeFile(join(root, "clients.json"), JSON.stringify([client]), { mode: 0o600 });
  const metadataFile = join(root, "metadata.json");
  const originalFile = join(root, "synthetic-knowledge.txt");
  await writeFile(
    metadataFile,
    JSON.stringify({ title: "合成Wiki", collection_id: "wiki", source_kind: "wiki" }),
  );
  await writeFile(
    originalFile,
    "在庫連携の更新時刻を確認します。\n\nこれは接続検証用の合成Wikiです。",
  );
  // Exercise the same authenticated multipart helper documented for local API callers.
  const caller = Bun.spawn(
    [
      process.execPath,
      join(repo, "knowledge-api/scripts/client.ts"),
      "POST",
      "/api/v1/sources",
      metadataFile,
      "--upload",
      originalFile,
    ],
    {
      cwd: join(repo, "knowledge-api"),
      env: {
        ...process.env,
        KNOWLEDGE_CLIENTS_FILE: join(root, "clients.json"),
        KNOWLEDGE_API_URL: url,
      },
      stdout: "pipe",
      stderr: "pipe",
    },
  );
  const [output, , callerExit] = await Promise.all([
    new Response(caller.stdout).text(),
    new Response(caller.stderr).text(),
    caller.exited,
  ]);
  if (callerExit) {
    let code = "unknown";
    try {
      const error = errorResponse.safeParse(JSON.parse(output));
      if (error.success) code = error.data.error.code;
    } catch {}
    throw new Error(`Authenticated upload helper failed (${code})`);
  }
  const saved = registrationResponse.parse(JSON.parse(output));
  await api.worker.tick();
  if (store.job(saved.job_id)?.state !== "completed")
    throw new Error(`Extraction failed: ${store.job(saved.job_id)?.error_code}`);
  const search = searchResponse.parse(
    await (
      await request("/api/v1/search", "POST", {
        query: "在庫",
        mode: "text",
        scope: { collection_ids: ["wiki"] },
      })
    ).json(),
  );
  if (!search.results.length) throw new Error("Real Docling text was not searchable");
  const references = search.results.map((h) => ({
    source_id: h.source_id,
    source_revision: h.source_revision,
    evidence_revision: h.evidence_revision,
    context_id: h.context_id,
  }));
  const context = contextResponse.parse(
    await (
      await request("/api/v1/context", "POST", { retrieval_id: search.retrieval_id, references })
    ).json(),
  );
  if (!context.contexts.some((c) => c.text.includes("在庫連携")))
    throw new Error("Missing real context");
  const info = await request(`/api/v1/sources/${saved.source_id}`);
  sourceResponse.parse(await info.clone().json());
  await request(`/api/v1/sources/${saved.source_id}`, "DELETE", undefined, {
    "if-match": info.headers.get("etag") ?? "",
  });
  if (
    (
      await fetch(`${url}/api/v1/sources/${saved.source_id}/content`, {
        headers: { authorization: `Bearer ${client.token}` },
      })
    ).status !== 404
  )
    throw new Error("Deleted original was still accessible");
  await api.worker.tick();
  console.log(
    "HTTP smoke passed: upload → Python/Docling → SQLite FTS → context → immediate deletion → cleanup.",
  );
} finally {
  await server.stop(true);
  processor.kill("SIGTERM");
  const exited = await Promise.race([processor.exited, Bun.sleep(10000).then(() => null)]);
  if (exited === null) {
    processor.kill("SIGKILL");
    await processor.exited;
  }
  const errors = await new Response(processor.stderr).text();
  if (process.exitCode || errors.includes("Traceback")) console.error(errors);
  store.db.close();
  await rm(root, { recursive: true, force: true });
}
