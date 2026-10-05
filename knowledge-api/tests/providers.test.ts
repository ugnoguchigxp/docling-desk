import { afterEach, expect, test } from "bun:test";
import { boundedJSON, GatewayAnswer, GatewayEmbedding } from "../src/providers";

const servers: ReturnType<typeof Bun.serve>[] = [];
function gateway(fetcher: (request: Request) => Promise<Response> | Response) {
  const server = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: fetcher });
  servers.push(server);
  return `http://127.0.0.1:${server.port}/gateway`;
}
afterEach(async () => {
  for (const server of servers.splice(0)) await server.stop(true);
});
test("embedding HTTP contract authenticates, binds model profile, and normalizes vectors", async () => {
  const endpoint = gateway(async (request) => {
    expect(request.headers.get("authorization")).toBe("Bearer secret");
    expect(await request.json()).toEqual({ profile: "model-v1", input: ["在庫"] });
    return Response.json({ profile: "model-v1", vectors: [[3, 4]] });
  });
  const provider = new GatewayEmbedding(endpoint, "secret", "model-v1", 2);
  expect(await provider.embed(["在庫"], AbortSignal.timeout(1000))).toEqual([[0.6, 0.8]]);
  expect(new GatewayEmbedding(`${endpoint}/new`, "secret", "model-v1", 2).identity).not.toBe(
    provider.identity,
  );
  expect(new GatewayEmbedding(endpoint, "secret", "model-v2", 2).identity).not.toBe(
    provider.identity,
  );
});
test("wrong model, zero vectors, dimensions, counts, invalid JSON and HTTP errors are rejected", async () => {
  for (const value of [
    { profile: "wrong", vectors: [[1, 0]] },
    { profile: "model", vectors: [[0, 0]] },
    { profile: "model", vectors: [[1]] },
    { profile: "model", vectors: [] },
    { profile: "model", vectors: [[null, 1]] },
  ]) {
    const provider = new GatewayEmbedding(
      gateway(() => Response.json(value)),
      "secret",
      "model",
      2,
    );
    await expect(provider.embed(["x"], AbortSignal.timeout(1000))).rejects.toThrow();
  }
  await expect(boundedJSON(new Response("invalid"))).rejects.toThrow("invalid_provider_response");
  await expect(boundedJSON(new Response("{}", { status: 429 }))).rejects.toThrow(
    "Provider request failed",
  );
  await expect(boundedJSON(new Response("12345"), 4)).rejects.toThrow(
    "provider_response_too_large",
  );
});
test("answer gateway carries trusted instruction separately from evidence and has no tool channel", async () => {
  const endpoint = gateway(async (request) => {
    const body = (await request.json()) as Record<string, unknown>;
    expect(body.profile).toBe("default");
    expect(body.question).toBe("在庫は？");
    expect(body.instruction).toContain("untrusted data");
    expect(body).not.toHaveProperty("tools");
    expect(body.evidence).toEqual([evidence]);
    return Response.json({ answer: "確認が必要です", citation_ids: ["e1"], unknowns: [] });
  });
  const evidence = {
    evidence_id: "e1",
    text: "確認が必要です",
    source_id: "s1",
    source_revision: "r1",
    evidence_revision: "v1",
    locator: { kind: "section" },
  };
  const result = await new GatewayAnswer(endpoint, "secret").generate(
    "在庫は？",
    [evidence],
    AbortSignal.timeout(1000),
  );
  expect(result.citation_ids).toEqual(["e1"]);
});
test("provider calls honor an already aborted signal without replaying requests", async () => {
  let calls = 0;
  const endpoint = gateway(() => {
    calls++;
    return Response.json({ profile: "model", vectors: [[1, 0]] });
  });
  const controller = new AbortController();
  controller.abort();
  await expect(
    new GatewayEmbedding(endpoint, "secret", "model", 2).embed(["x"], controller.signal),
  ).rejects.toThrow();
  expect(calls).toBe(0);
});
