// Isolated real API/SQLite/worker for the Python MCP transport acceptance test.
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createApi } from "../../knowledge-api/src/app";
import { clientSchema } from "../../knowledge-api/src/auth";
import { HttpExtractor } from "../../knowledge-api/src/providers";
import { Store } from "../../knowledge-api/src/store";

const config = JSON.parse(await readFile(Bun.argv[2]!, "utf8"));
const client = clientSchema.parse(config.client);
const store = new Store(join(config.root, "knowledge.sqlite"));
store.acquireLease();
const api = createApi({
  store,
  artifactRoot: config.root,
  clients: () => [client],
  extractor: new HttpExtractor(config.processor_url, config.processor_token),
});
const server = Bun.serve({
  hostname: "127.0.0.1", port: 0, fetch: api.app.fetch,
  maxRequestBodySize: 51 * 1024 * 1024, idleTimeout: 60,
});
const timer = setInterval(() => void api.worker.tick(), 50);
const lease = setInterval(() => store.renewLease(), 10000);
console.log(JSON.stringify({ url: `http://127.0.0.1:${server.port}` }));
process.on("SIGTERM", async () => {
  clearInterval(timer);
  clearInterval(lease);
  api.worker.stop();
  await server.stop(true);
  store.releaseLease();
  store.db.close();
  process.exit(0);
});
