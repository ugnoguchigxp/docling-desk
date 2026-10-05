import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(process.env.KNOWLEDGE_STATE_DIR ?? "../.knowledge-api");
for (const name of ["clients.json", "runtime.env", "container.env", "worker.env"]) {
  if (existsSync(`${root}/${name}`))
    throw new Error("Existing local configuration is never overwritten");
}
mkdirSync(root, { recursive: true, mode: 0o700 });
const token = randomBytes(32).toString("hex");
const clients = [
  {
    id: "local-main",
    token: randomBytes(32).toString("hex"),
    issuer: "local-main",
    keys: { "local-v1": randomBytes(32).toString("hex") },
    actions: ["read", "write", "answer"],
    mode: "actor",
    scopes: [
      { collection_id: "wiki" },
      { collection_id: "assessment", project_id: "demo", region: "JP" },
    ],
  },
];
writeFileSync(`${root}/clients.json`, JSON.stringify(clients, null, 2), {
  mode: 0o600,
  flag: "wx",
});
writeFileSync(
  `${root}/runtime.env`,
  `KNOWLEDGE_STATE_DIR=${root}\nKNOWLEDGE_ARTIFACT_ROOT=${root}/artifacts\nKNOWLEDGE_CLIENTS_FILE=${root}/clients.json\nKNOWLEDGE_WORKER_TOKEN=${token}\n`,
  { mode: 0o600, flag: "wx" },
);
writeFileSync(
  `${root}/container.env`,
  `KNOWLEDGE_WORKER_TOKEN=${token}\nKNOWLEDGE_CLIENTS_B64=${Buffer.from(JSON.stringify(clients)).toString("base64")}\n`,
  { mode: 0o600, flag: "wx" },
);
writeFileSync(`${root}/worker.env`, `KNOWLEDGE_WORKER_TOKEN=${token}\n`, {
  mode: 0o600,
  flag: "wx",
});
console.log(
  `Local configuration created under ${root}; existing credentials are never overwritten.`,
);
