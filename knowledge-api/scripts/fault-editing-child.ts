/** Isolated crash probes invoke the same public HTTP business operations. */
import { join } from "node:path";
import { createApi } from "../src/app";
import { clientSchema } from "../src/auth";
import { Store } from "../src/store";

const root = process.argv[2];
if (!root) throw new Error("root required");
const client = clientSchema.parse({
  id: "probe",
  token: "p".repeat(40),
  issuer: "probe",
  keys: {},
  mode: "batch",
  actions: ["read", "write"],
  scopes: [{ collection_id: "probe" }],
});
const store = new Store(join(root, "knowledge.sqlite"));
const api = createApi({
  store,
  artifactRoot: root,
  clients: () => [client],
  extractor: {
    validate: async () => {},
    extract: async () => {
      throw new Error("unexpected extraction");
    },
  },
});
const headers = {
  authorization: "Bearer " + client.token,
  "content-type": "application/json",
  "idempotency-key": "durable-operation",
};
let response: Response;
if (process.argv[3] === "attachment") {
  const draftResponse = await api.app.request("/api/v1/drafts", {
    method: "POST",
    headers,
    body: JSON.stringify({ body: "image", metadata: { title: "Probe", collection_id: "probe" } }),
  });
  if (!draftResponse.ok) throw new Error("draft failed");
  const draft = await draftResponse.json();
  response = await api.app.request(`/api/v1/drafts/${draft.draft_id}/attachments`, {
    method: "POST",
    headers: { ...headers, "if-match": `"${draft.draft_id}:1"` },
    body: JSON.stringify({
      filename: "image.png",
      media_type: "image/png",
      body_base64: Buffer.from("immutable image").toString("base64"),
    }),
  });
} else {
  response = await api.app.request("/api/v1/markdown-sources", {
    method: "POST",
    headers,
    body: JSON.stringify({
      body: "\ufeff---\r\ntitle: durable\r\n---\r\n本文 😀\r\n",
      metadata: { title: "Probe", collection_id: "probe" },
    }),
  });
}
if (!response.ok) throw new Error("save failed " + response.status);
console.log(JSON.stringify(await response.json()));
store.db.close();
