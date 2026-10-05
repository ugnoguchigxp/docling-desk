import { createHmac, randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { parseArgs } from "node:util";
import { clientSchema } from "../src/auth";

const { values: flags, positionals } = parseArgs({
  args: process.argv.slice(2),
  allowPositionals: true,
  options: {
    upload: { type: "string" },
    etag: { type: "string" },
    idempotency: { type: "string" },
    output: { type: "string" },
  },
});
const [method, path, bodyFile] = positionals;
if (!method || !path || !path.startsWith("/api/v1/") || positionals.length > 3)
  throw new Error(
    "Usage: bun scripts/client.ts METHOD /api/v1/path [JSON-file] [--upload file] [--etag value] [--idempotency key] [--output file]",
  );
const clients = JSON.parse(
  readFileSync(
    resolve(process.env.KNOWLEDGE_CLIENTS_FILE ?? "../.knowledge-api/clients.json"),
    "utf8",
  ),
);
const client = clientSchema.parse(clients[0]);
const headers: Record<string, string> = { authorization: `Bearer ${client.token}` };
if (client.mode === "actor") {
  const now = Math.floor(Date.now() / 1000);
  const entry = Object.entries(client.keys)[0];
  if (!entry) throw new Error("Actor signing key required");
  const [kid, key] = entry;
  const h = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT", kid })).toString("base64url");
  const p = Buffer.from(
    JSON.stringify({
      iss: client.issuer,
      aud: "docling-desk-api",
      sub: "local-user",
      client_id: client.id,
      iat: now,
      exp: now + 300,
      scopes: client.scopes,
    }),
  ).toString("base64url");
  headers["x-knowledge-actor"] =
    `${h}.${p}.${createHmac("sha256", key).update(`${h}.${p}`).digest("base64url")}`;
}
if (flags.etag) headers["if-match"] = flags.etag;
if (flags.idempotency) headers["idempotency-key"] = flags.idempotency;
let body: BodyInit | undefined;
if (flags.upload) {
  if (!bodyFile || method.toUpperCase() !== "POST")
    throw new Error("--upload requires POST and a metadata JSON file");
  const form = new FormData();
  form.append("file", new File([readFileSync(flags.upload)], basename(flags.upload)));
  form.append("metadata", readFileSync(bodyFile, "utf8"));
  body = form;
  headers["idempotency-key"] ??= randomUUID();
} else if (bodyFile) {
  headers["content-type"] = "application/json";
  body = readFileSync(bodyFile);
}
const response = await fetch(
  `${process.env.KNOWLEDGE_API_URL ?? "http://127.0.0.1:18766"}${path}`,
  { method, headers, body, redirect: "error" },
);
const etag = response.headers.get("etag");
if (etag) console.error(`ETag: ${etag}`);
if (flags.output && response.ok)
  writeFileSync(flags.output, new Uint8Array(await response.arrayBuffer()), {
    flag: "wx",
    mode: 0o600,
  });
else console.log(await response.text());
if (!response.ok) process.exitCode = 1;
