import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { z } from "zod";
import { clientSchema } from "../src/auth";

const root = resolve(process.env.KNOWLEDGE_STATE_DIR ?? "../.knowledge-api");
const clients = z
  .array(clientSchema)
  .min(1)
  .parse(JSON.parse(readFileSync(`${root}/clients.json`, "utf8")));
const token = readFileSync(`${root}/runtime.env`, "utf8").match(
  /^KNOWLEDGE_WORKER_TOKEN=([a-f0-9]{64})$/m,
)?.[1];
if (!token) throw new Error("Generated worker token is required");

function updateConfig(filename: string, generated: Record<string, string>) {
  const existing = existsSync(filename) ? readFileSync(filename, "utf8") : "";
  const retained = existing
    .split(/\r?\n/)
    .filter((line) => {
      const key = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=/)?.[1];
      return !key || !(key in generated);
    })
    .join("\n");
  const separator = retained && !retained.endsWith("\n") ? "\n" : "";
  writeFileSync(
    filename,
    retained +
      separator +
      Object.entries(generated)
        .map(([key, value]) => `${key}=${value}\n`)
        .join(""),
    { mode: 0o600 },
  );
  chmodSync(filename, 0o600);
}

updateConfig(`${root}/container.env`, {
  KNOWLEDGE_WORKER_TOKEN: token,
  KNOWLEDGE_CLIENTS_B64: Buffer.from(JSON.stringify(clients)).toString("base64"),
});
updateConfig(`${root}/worker.env`, { KNOWLEDGE_WORKER_TOKEN: token });
console.log("Container credentials synchronized; recreate API and processor containers to apply.");
