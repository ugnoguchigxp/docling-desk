import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { z } from "zod";
import { type Clients, clientSchema } from "./auth";
import { ApiError } from "./contracts";
import { GatewayAnswer, GatewayEmbedding, HttpExtractor } from "./providers";

function endpoint(raw: string, internal = false) {
  const url = new URL(raw);
  if (
    url.username ||
    url.password ||
    url.hash ||
    url.search ||
    (!internal &&
      url.protocol !== "https:" &&
      !(url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname))) ||
    !["http:", "https:"].includes(url.protocol)
  )
    throw new Error("Invalid configured endpoint");
  return url.href.replace(/\/$/, "");
}
async function lifecycleResponse(response: Response) {
  const reader = response.body?.getReader();
  if (!reader) throw new ApiError(503, "processor_activity_unavailable");
  let size = 0,
    text = "";
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 4096) throw new Error("response_too_large");
      text += decoder.decode(chunk.value, { stream: true });
    }
    return z
      .object({ bootId: z.string().uuid(), activeJobs: z.number().int().nonnegative() })
      .strict()
      .parse(JSON.parse(text + decoder.decode()));
  } catch {
    throw new ApiError(503, "processor_activity_unavailable");
  } finally {
    await reader.cancel().catch(() => {});
  }
}
export function loadConfig(env: NodeJS.ProcessEnv = process.env) {
  const root = resolve(env.KNOWLEDGE_STATE_DIR ?? "../.knowledge-api");
  const artifactRoot = resolve(env.KNOWLEDGE_ARTIFACT_ROOT ?? `${root}/artifacts`);
  const clientsFile = resolve(env.KNOWLEDGE_CLIENTS_FILE ?? `${root}/clients.json`);
  const clients: Clients = () => {
    try {
      const raw = env.KNOWLEDGE_CLIENTS_B64
        ? Buffer.from(env.KNOWLEDGE_CLIENTS_B64, "base64").toString("utf8")
        : readFileSync(clientsFile, "utf8");
      const values = z.array(clientSchema).min(1).max(100).parse(JSON.parse(raw));
      if (
        new Set(values.map((v) => v.id)).size !== values.length ||
        new Set(values.map((v) => v.token)).size !== values.length ||
        values.some((v) => v.mode === "actor" && !Object.keys(v.keys).length)
      )
        throw new Error();
      return values;
    } catch {
      throw new ApiError(503, "client_configuration_invalid");
    }
  };
  clients();
  const secret = (name: string) => {
    const file = env[`${name}_FILE`];
    return file ? readFileSync(file, "utf8").trim() : (env[name] ?? "");
  };
  const token = secret("KNOWLEDGE_WORKER_TOKEN");
  const larmToken = secret("KNOWLEDGE_LARM_TOKEN");
  if (larmToken && (larmToken.length < 32 || larmToken.length > 4096 || /\s/.test(larmToken)))
    throw new Error("KNOWLEDGE_LARM_TOKEN must be a 32 to 4096 character token without whitespace");
  const processorEndpoint = endpoint(env.KNOWLEDGE_WORKER_URL ?? "http://127.0.0.1:18767", true);
  if (token.length < 32)
    throw new Error("KNOWLEDGE_WORKER_TOKEN must contain at least 32 characters");
  const embedding = env.KNOWLEDGE_EMBEDDING_URL
    ? new GatewayEmbedding(
        endpoint(env.KNOWLEDGE_EMBEDDING_URL),
        env.KNOWLEDGE_PROVIDER_TOKEN ?? "",
        z.string().min(1).max(256).parse(env.KNOWLEDGE_EMBEDDING_PROFILE),
        z.coerce.number().int().min(1).max(8192).parse(env.KNOWLEDGE_EMBEDDING_DIMENSIONS),
      )
    : undefined;
  const answer = env.KNOWLEDGE_ANSWER_URL
    ? new GatewayAnswer(endpoint(env.KNOWLEDGE_ANSWER_URL), env.KNOWLEDGE_PROVIDER_TOKEN ?? "")
    : undefined;
  if ((embedding || answer) && !env.KNOWLEDGE_PROVIDER_TOKEN)
    throw new Error("KNOWLEDGE_PROVIDER_TOKEN is required");
  return {
    larmToken: larmToken || undefined,
    processorLifecycle: larmToken
      ? async (method: "activity" | "drain" | "resume") => {
          const r = await fetch(`${processorEndpoint}/internal/larm/${method}`, {
            method: method === "activity" ? "GET" : "POST",
            headers: { authorization: `Bearer ${larmToken}` },
            redirect: "error",
            signal: AbortSignal.timeout(3000),
          });
          if (!r.ok) {
            await r.body?.cancel();
            throw new ApiError(503, "processor_activity_unavailable");
          }
          return lifecycleResponse(r);
        }
      : undefined,
    root,
    artifactRoot,
    clients,
    embedding,
    answer,
    extractor: new HttpExtractor(
      endpoint(env.KNOWLEDGE_WORKER_URL ?? "http://127.0.0.1:18767", true),
      token,
    ),
    workerToken: token,
    ocrMonthlySubmissions: z.coerce
      .number()
      .int()
      .min(1)
      .max(10000000)
      .parse(env.KNOWLEDGE_OCR_MONTHLY_SUBMISSIONS ?? "10000"),
    hostname: env.KNOWLEDGE_HOST ?? "127.0.0.1",
    port: z.coerce
      .number()
      .int()
      .min(1)
      .max(65535)
      .parse(env.KNOWLEDGE_PORT ?? 18766),
  };
}
