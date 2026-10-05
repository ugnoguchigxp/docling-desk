import { randomUUID } from "node:crypto";
import { Store } from "../src/store";

const dbPath = process.argv[2];
if (!dbPath) throw new Error("database path is required");
const store = new Store(dbPath);
const source = randomUUID();
const revision = randomUUID();
const jobId = randomUUID();
const sha = "a".repeat(64);
const now = new Date().toISOString();
store.db.run("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,0,1,?,?,?)", [
  source,
  "Notes",
  "wiki",
  null,
  null,
  "document",
  "ja",
  revision,
  now,
  "main",
  null,
]);
store.db.run(
  "INSERT INTO revisions(id,source_id,filename,suffix,sha256,created_at) VALUES(?,?,?,?,?,?)",
  [revision, source, "notes.txt", ".txt", sha, now],
);
store.db.run(
  "INSERT INTO jobs(id,source_id,source_revision,kind,state,stage,run,lease_until,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
  [
    jobId,
    source,
    revision,
    "extract",
    "running",
    "extracting",
    1,
    Date.now() + 60000,
    "{}",
    now,
    now,
  ],
);
const job = store.job(jobId);
if (!job) throw new Error("job missing");
const record = {
  id: "ctx0",
  text: "在庫",
  parent_id: null,
  source_sha256: sha,
  kind: "section",
  headings: [],
  refs: [],
  context_refs: [],
  pages: [],
  provenance: [],
  relations: [],
  row_range: [],
  tables: [],
};
store.publish(job, {
  source_id: source,
  source_revision: revision,
  job_id: jobId,
  run: 1,
  source_sha256: sha,
  profile: "local-v1",
  contexts: [record],
  chunks: [{ ...record, id: "chunk0", parent_id: "ctx0" }],
  warnings: [],
});
console.log("published");
store.db.close();
