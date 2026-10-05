import { createHmac, randomBytes, randomUUID } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { z } from "zod";
import { clientSchema } from "../src/auth";
import {
  contextResponse,
  jobResponse,
  registrationResponse,
  searchResponse,
  sourceResponse,
} from "../src/responses";

// Every container and volume belongs to a fresh test project; existing demo containers are untouched.
const root = await mkdtemp(join(tmpdir(), "knowledge-docker-smoke-"));
const repo = resolve(import.meta.dir, "../..");
const project = `docling-desk-qa-${randomBytes(4).toString("hex")}`;
const reservation = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  fetch: () => new Response("reserved"),
});
const port = reservation.port;
await reservation.stop(true);
const env = {
  ...process.env,
  // Synthetic-document verification never inherits live model/OCR connections.
  KNOWLEDGE_EMBEDDING_URL: "",
  KNOWLEDGE_EMBEDDING_PROFILE: "",
  KNOWLEDGE_EMBEDDING_DIMENSIONS: "",
  KNOWLEDGE_ANSWER_URL: "",
  KNOWLEDGE_PROVIDER_TOKEN: "",
  DOCLING_AZURE_OCR_ENDPOINT: "",
  AZURE_DOCUMENT_INTELLIGENCE_API_KEY: "",
  KNOWLEDGE_STATE_DIR: root,
  KNOWLEDGE_PORT: String(port),
  KNOWLEDGE_CONTAINER_ENV: join(root, "container.env"),
  KNOWLEDGE_WORKER_ENV: join(root, "worker.env"),
};
async function run(args: string[]) {
  const process = Bun.spawn(args, { cwd: repo, env, stdout: "inherit", stderr: "inherit" });
  if (await process.exited) throw new Error(`Verification command failed: ${args[0]} ${args[1]}`);
}
const compose = [
  "docker",
  "compose",
  "-p",
  project,
  "-f",
  join(repo, "deploy/compose.knowledge.yml"),
];
const base = `http://127.0.0.1:${port}`;
try {
  await run(["bun", join(repo, "knowledge-api/scripts/setup-local.ts")]);
  await run([...compose, "config", "--quiet"]);
  console.log("Building isolated knowledge API and processor images.");
  await run([...compose, "build", "--quiet"]);
  await run([...compose, "up", "-d", "--wait", "--wait-timeout", "240"]);
  const clients = z
    .array(clientSchema)
    .parse(JSON.parse(await readFile(join(root, "clients.json"), "utf8")));
  const client = clientSchema.parse(clients[0]);
  if (!client) throw new Error("Missing test client");
  function headers() {
    const now = Math.floor(Date.now() / 1000);
    const entry = Object.entries(client.keys)[0];
    if (!entry) throw new Error("Missing actor key");
    const [kid, key] = entry;
    const h = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT", kid })).toString("base64url");
    const p = Buffer.from(
      JSON.stringify({
        iss: client.issuer,
        aud: "docling-desk-api",
        sub: "qa",
        client_id: client.id,
        iat: now,
        exp: now + 300,
        scopes: client.scopes,
      }),
    ).toString("base64url");
    return {
      authorization: `Bearer ${client.token}`,
      "x-knowledge-actor": `${h}.${p}.${createHmac("sha256", key).update(`${h}.${p}`).digest("base64url")}`,
    };
  }
  async function request(
    path: string,
    method = "GET",
    value?: unknown,
    extra: Record<string, string> = {},
  ) {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { ...headers(), ...(value ? { "content-type": "application/json" } : {}), ...extra },
      body: value ? JSON.stringify(value) : undefined,
    });
    if (!response.ok)
      throw new Error(`API verification failed: ${response.status} ${await response.text()}`);
    return response;
  }
  async function finished(jobId: string) {
    const deadline = Date.now() + 360000;
    while (Date.now() < deadline) {
      const job = jobResponse.parse(await (await request(`/api/v1/jobs/${jobId}`)).json());
      if (job.state === "completed") return;
      if (!["queued", "running"].includes(job.state))
        throw new Error(`Job failed: ${job.error_code}`);
      await Bun.sleep(1000);
    }
    throw new Error("Document processing timed out");
  }
  const docs = [
    {
      name: "synthetic-api.txt",
      bytes: Buffer.from("在庫連携の時刻を確認します。"),
      query: "在庫",
      kind: "wiki",
    },
    {
      name: "synthetic-api.md",
      bytes: Buffer.from("# 合成Wiki\n\n| 区分 | 件数 |\n|---|---|\n| SKU-001 | 450 |\n"),
      query: "450",
      kind: "wiki",
    },
    ...(await Promise.all(
      [
        "synthetic-report.pdf",
        "synthetic-report.docx",
        "synthetic-sheet.xlsx",
        "synthetic-slides.pptx",
      ].map(async (name) => ({
        name,
        bytes: await readFile(join(repo, "samples", name)),
        query: name.endsWith(".pptx") ? "合成" : "450",
        kind: "document",
      })),
    )),
  ];
  for (const doc of docs) {
    const form = new FormData();
    form.append("file", new File([doc.bytes], doc.name));
    form.append(
      "metadata",
      JSON.stringify({ title: doc.name, collection_id: "wiki", source_kind: doc.kind }),
    );
    const response = await fetch(`${base}/api/v1/sources`, {
      method: "POST",
      headers: { ...headers(), "idempotency-key": randomUUID() },
      body: form,
    });
    if (response.status !== 202)
      throw new Error(`Upload failed: ${response.status} ${await response.text()}`);
    const saved = registrationResponse.parse(await response.json());
    await finished(saved.job_id);
    const searched = searchResponse.parse(
      await (
        await request("/api/v1/search", "POST", {
          query: doc.query,
          mode: "text",
          scope: { collection_ids: ["wiki"] },
        })
      ).json(),
    );
    const hits = searched.results.filter((h) => h.source_id === saved.source_id);
    if (!hits.length) throw new Error(`No real search result for ${doc.name}`);
    const references = hits.map((h) => ({
      source_id: h.source_id,
      source_revision: h.source_revision,
      evidence_revision: h.evidence_revision,
      context_id: h.context_id,
    }));
    const context = contextResponse.parse(
      await (
        await request("/api/v1/context", "POST", {
          retrieval_id: searched.retrieval_id,
          references,
        })
      ).json(),
    );
    if (!context.contexts.some((c) => c.text.includes(doc.query)))
      throw new Error(`Missing source context for ${doc.name}`);
    if (
      [".md", ".pdf", ".docx", ".xlsx"].some((ext) => doc.name.endsWith(ext)) &&
      !context.contexts.some((c) => c.tables.length)
    )
      throw new Error(`Structured table was not returned for ${doc.name}`);
    const info = await request(`/api/v1/sources/${saved.source_id}`);
    sourceResponse.parse(await info.clone().json());
    const removal = await request(`/api/v1/sources/${saved.source_id}`, "DELETE", undefined, {
      "if-match": info.headers.get("etag") ?? "",
    });
    const deleted = (await removal.json()) as { job_id: string };
    if (
      (await fetch(`${base}/api/v1/sources/${saved.source_id}/content`, { headers: headers() }))
        .status !== 404
    )
      throw new Error("Deleted source was accessible");
    await finished(deleted.job_id);
    console.log(`Container API passed: ${doc.name}; search, context, tables/provenance, deletion.`);
  }
} finally {
  await run([...compose, "down", "--volumes", "--remove-orphans"]);
  await rm(root, { recursive: true, force: true });
}
