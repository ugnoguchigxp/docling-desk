import { createHmac, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { ApiError, type Grant, grantSchema, type Principal, type Scope } from "./contracts";

export const clientSchema = z
  .object({
    id: z.string().min(1).max(128),
    token: z.string().regex(/^[A-Za-z0-9._~-]{32,256}$/),
    issuer: z.string().min(1),
    keys: z.record(z.string(), z.string().min(32)),
    scopes: z.array(grantSchema).min(1).max(100),
    actions: z.array(z.enum(["read", "write", "answer"])).min(1),
    mode: z.enum(["actor", "batch"]).default("actor"),
  })
  .strict();
export type Client = z.infer<typeof clientSchema>;
export type Clients = () => Client[];
function credentialVersion(c: Client) {
  return Bun.CryptoHasher.hash(
    "sha256",
    JSON.stringify([c.id, c.token, c.issuer, c.keys, c.mode, c.scopes, c.actions]),
    "hex",
  );
}
const assertionSchema = z
  .object({
    iss: z.string(),
    aud: z.literal("docling-desk-api"),
    sub: z.string().min(1).max(256),
    client_id: z.string(),
    exp: z.number().int(),
    iat: z.number().int(),
    scopes: z.array(grantSchema).min(1).max(100),
  })
  .strict();
function equal(a: string, b: string) {
  const aa = Buffer.from(a);
  const bb = Buffer.from(b);
  return aa.length === bb.length && timingSafeEqual(aa, bb);
}
export function sameGrant(a: Grant, b: Grant) {
  return (
    a.collection_id === b.collection_id && a.project_id === b.project_id && a.region === b.region
  );
}
export function authenticate(
  headers: Headers,
  clients: Client[],
  now = Math.floor(Date.now() / 1000),
): Principal {
  const token = headers.get("authorization")?.match(/^Bearer (.+)$/i)?.[1] ?? "";
  const client = clients.find((c) => equal(c.token, token));
  if (!client) throw new ApiError(401, "unauthenticated");
  if (client.mode === "batch")
    return {
      clientId: client.id,
      subject: `service:${client.id}`,
      grants: client.scopes,
      actions: client.actions,
      expires: now + 300,
      credential_version: credentialVersion(client),
    };
  try {
    const pieces = (headers.get("x-knowledge-actor") ?? "").split(".");
    if (pieces.length !== 3 || pieces.some((v) => !v || v.length > 32000)) throw new Error();
    const [h, p, s] = pieces as [string, string, string];
    const header = z
      .object({ alg: z.literal("HS256"), typ: z.literal("JWT"), kid: z.string() })
      .strict()
      .parse(JSON.parse(Buffer.from(h, "base64url").toString()));
    const key = client.keys[header.kid];
    if (!key || !equal(createHmac("sha256", key).update(`${h}.${p}`).digest("base64url"), s))
      throw new Error();
    const claims = assertionSchema.parse(JSON.parse(Buffer.from(p, "base64url").toString()));
    if (
      claims.iss !== client.issuer ||
      claims.client_id !== client.id ||
      claims.exp <= now ||
      claims.iat > now + 30 ||
      claims.iat < now - 300 ||
      claims.exp - claims.iat > 300 ||
      claims.exp <= claims.iat ||
      !claims.scopes.every((g) => client.scopes.some((cg) => sameGrant(g, cg)))
    )
      throw new Error();
    return {
      clientId: client.id,
      subject: claims.sub,
      grants: claims.scopes,
      actions: client.actions,
      expires: claims.exp,
      credential_version: credentialVersion(client),
    };
  } catch {
    throw new ApiError(401, "invalid_actor");
  }
}
export function requireAction(p: Principal, action: string) {
  if (!p.actions.includes(action)) throw new ApiError(403, "forbidden");
}
export function authorized(
  p: Principal,
  source: { collection_id: string; project_id?: string | null; region?: string | null },
) {
  return p.grants.some(
    (g) =>
      g.collection_id === source.collection_id &&
      (g.project_id ?? null) === (source.project_id ?? null) &&
      (g.region ?? null) === (source.region ?? null),
  );
}
export function narrow(p: Principal, scope: Scope): Grant[] {
  const grants = p.grants.filter(
    (g) =>
      scope.collection_ids.includes(g.collection_id) &&
      (!g.project_id || scope.project_id === g.project_id) &&
      (!g.region || scope.region === g.region),
  );
  if (scope.collection_ids.some((c) => !grants.some((g) => g.collection_id === c)))
    throw new ApiError(403, "scope_forbidden");
  return grants;
}
export function refresh(p: Principal, clients: Client[]): Principal {
  const client = clients.find((c) => c.id === p.clientId);
  if (
    !client ||
    p.credential_version !== credentialVersion(client) ||
    p.expires <= Math.floor(Date.now() / 1000) ||
    !p.grants.every((g) => client.scopes.some((cg) => sameGrant(g, cg)))
  )
    throw new ApiError(401, "authorization_expired");
  return { ...p, actions: client.actions };
}
export function grantKey(g: Grant) {
  return JSON.stringify([g.collection_id, g.project_id ?? null, g.region ?? null]);
}
