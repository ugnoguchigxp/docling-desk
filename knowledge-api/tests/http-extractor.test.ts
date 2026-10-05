import { afterEach, expect, test } from "bun:test";
import { ApiError } from "../src/contracts";
import { HttpExtractor } from "../src/providers";
import type { JobRow, RevisionRow } from "../src/store";

const servers: ReturnType<typeof Bun.serve>[] = [];
function serve(fetch: (request: Request) => Promise<Response> | Response) {
  const server = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch });
  servers.push(server);
  return `http://127.0.0.1:${server.port}`;
}
afterEach(async () => {
  for (const server of servers.splice(0)) await server.stop(true);
});

const id = "11111111-1111-4111-8111-111111111111";
const sha = "a".repeat(64);
const profile = {
  provider: "local",
  enabled: true,
  endpoint: "",
  auth: "api_key",
  client_id: null,
  api_version: "2024-11-30",
  model: "prebuilt-read",
  profile: "read-v1",
  tier: "F0",
  document_timeout: 180,
  max_submissions: 1,
  adapter_version: "1",
};
const input = {
  source_id: id,
  source_revision: id,
  job_id: id,
  run: 1,
  suffix: ".txt",
  resource: "manifest",
  prefix: "/viewer/session/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/",
};
const job = {
  id,
  source_id: id,
  run: 1,
  payload: JSON.stringify({ profile: "local-v1", allow_ocr_resubmit: true }),
} as JobRow;
const revision = { id, suffix: ".txt", sha256: sha, filename: "notes.txt" } as RevisionRow;

test("HTTP extractor covers viewer, readiness, OCR profile, validation, and extract", async () => {
  const endpoint = serve(async (request) => {
    if (request.url.endsWith("/health/ready")) return new Response("ok");
    expect(request.headers.get("authorization")).toBe("Bearer secret");
    if (request.url.endsWith("/viewer"))
      return Response.json({
        job: {
          id,
          filename: "notes.txt",
          state: "success",
          pages: 1,
          preview: "p",
          slide_layout: false,
        },
        kind: "document",
        units: 1,
      });
    if (request.url.endsWith("/ocr-profile")) {
      const body = (await request.json()) as { provider?: string };
      return Response.json({
        profile: body.provider ? "azure-read-v1" : "local-v1",
        ocr_profile: profile,
      });
    }
    if (request.url.endsWith("/validate")) return Response.json({ valid: true, sha256: sha });
    const body = (await request.json()) as { ocr_profile?: unknown };
    expect(body.ocr_profile).toBeUndefined();
    return Response.json({
      source_id: id,
      source_revision: id,
      job_id: id,
      run: 1,
      source_sha256: sha,
      profile: "local-v1",
      contexts: [{ id: "c", text: "本文", source_sha256: sha, kind: "text" }],
      chunks: [],
    });
  });
  const extractor = new HttpExtractor(endpoint, "secret");
  expect(await extractor.ready()).toBe(true);
  expect(await extractor.viewer(input)).toMatchObject({ kind: "document" });
  expect((await extractor.ocrProfile()).profile).toBe("local-v1");
  expect((await extractor.ocrProfile("azure_read")).profile).toBe("azure-read-v1");
  await extractor.validate({ source_id: id, source_revision: id, suffix: ".txt", sha256: sha });
  expect((await extractor.extract(job, revision, AbortSignal.timeout(1000))).chunks).toEqual([]);
});

test("HTTP extractor reports viewer conflicts, unhealthy checks, and rejected documents", async () => {
  const down = new HttpExtractor("http://127.0.0.1:1", "secret");
  expect(await down.ready()).toBe(false);
  const endpoint = serve(async (request) => {
    if (request.url.endsWith("/health/ready")) return new Response("no", { status: 503 });
    if (request.url.endsWith("/viewer")) return new Response("missing", { status: 404 });
    if (request.url.endsWith("/ocr-profile")) return Response.json({ profile: "nope" });
    if (request.url.endsWith("/validate")) return new Response("not json", { status: 422 });
    return Response.json({ detail: "ocr_queue_full" }, { status: 422 });
  });
  const extractor = new HttpExtractor(endpoint, "secret");
  expect(await extractor.ready()).toBe(false);
  await expect(extractor.viewer(input)).rejects.toMatchObject({
    status: 404,
    code: "viewer_resource_unavailable",
  });
  await expect(extractor.ocrProfile()).rejects.toBeInstanceOf(ApiError);
  await expect(
    extractor.validate({ source_id: id, source_revision: id, suffix: ".txt", sha256: sha }),
  ).rejects.toMatchObject({ code: "invalid_document" });
  const unsafe = new HttpExtractor(
    serve(() => Response.json({ detail: "<script>" }, { status: 422 })),
    "secret",
  );
  await expect(unsafe.extract(job, revision, AbortSignal.timeout(1000))).rejects.toMatchObject({
    code: "invalid_document",
  });
  const named = new HttpExtractor(
    serve(() => Response.json({ detail: "submission_unknown" }, { status: 422 })),
    "secret",
  );
  await expect(
    named.extract(
      { ...job, payload: JSON.stringify({ ocr_profile: profile }) } as JobRow,
      revision,
      AbortSignal.timeout(1000),
    ),
  ).rejects.toMatchObject({ code: "submission_unknown" });
  const conflict = new HttpExtractor(
    serve(() => new Response("busy", { status: 409 })),
    "secret",
  );
  await expect(extractor.viewer(input)).rejects.toMatchObject({ status: 404 });
  await expect(conflict.viewer(input)).rejects.toMatchObject({ status: 409 });
});
