import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import { createApi } from "./app";
import { loadConfig } from "./config";
import { MAX_FILE } from "./contracts";
import { Store } from "./store";

const config = loadConfig();
await mkdir(config.artifactRoot, { recursive: true });
const store = new Store(join(config.root, "knowledge.sqlite"));
store.acquireLease();
const api = createApi({
  ...config,
  store,
  clients: () => {
    store.assertLeader();
    return config.clients();
  },
});
// An interrupted generation is never replayed automatically. It may already have been billed.
store.db.run(
  "UPDATE answers SET state='failed',result=NULL,error_code='interrupted' WHERE id IN (SELECT id FROM jobs WHERE kind='answer' AND state='running')",
);
store.db.run(
  "UPDATE jobs SET state='failed',error_code='interrupted',lease_until=0 WHERE kind='answer' AND state='running'",
);

// Files are written before DB registration. Clean only old, unregistered revisions, never in-flight uploads.
const sourceRoot = join(config.artifactRoot, "sources");
await mkdir(sourceRoot, { recursive: true });
for (const source of await readdir(sourceRoot)) {
  if (!/^[a-f0-9-]{36}$/.test(source)) continue;
  const sourceDir = join(sourceRoot, source);
  if (!(await stat(sourceDir)).isDirectory()) continue;
  for (const revision of await readdir(sourceDir)) {
    if (!/^[a-f0-9-]{36}$/.test(revision)) continue;
    const folder = join(sourceDir, revision);
    if (!store.revision(revision) && (await stat(folder)).mtimeMs < Date.now() - 3600000)
      await rm(folder, { recursive: true, force: true });
  }
}
const server = Bun.serve({
  hostname: config.hostname,
  port: config.port,
  fetch: api.app.fetch,
  maxRequestBodySize: MAX_FILE + 1024 * 1024,
  idleTimeout: 60,
});
const timer = setInterval(() => {
  void api.worker.tick().catch(() => console.error("Knowledge worker cycle failed"));
}, 250);
const pruneTimer = setInterval(() => store.prune(), 60000);
const leaseTimer = setInterval(() => {
  try {
    store.renewLease();
  } catch {
    void shutdown();
  }
}, 10000);
console.log(`Knowledge API listening on ${config.hostname}:${server.port}`);
let stopping = false;
async function shutdown() {
  if (stopping) return;
  stopping = true;
  clearInterval(timer);
  clearInterval(pruneTimer);
  clearInterval(leaseTimer);
  api.worker.stop();
  await server.stop();
  // Let the worker persist an interrupted status before closing its database.
  for (
    let i = 0;
    i < 100 &&
    store.db.query<{ n: number }, []>("SELECT count(*) n FROM jobs WHERE state='running'").get()?.n;
    i++
  )
    await Bun.sleep(100);
  store.releaseLease();
  process.exit(0);
}
process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());
