import { afterEach, beforeEach, expect, test, vi } from "vitest";

const login = vi.hoisted(() => ({ leaveForLogin: vi.fn() }));
vi.mock("./api", () => login);

import {
  DiagramRequestError,
  commitDraft,
  createDiagram,
  createDraft,
  diagramContentUrl,
  discardDraft,
  existingDraftId,
  getContent,
  getDiagram,
  listRevisions,
  restoreRevision,
  saveRevision,
  sha256Hex,
  updateDraft,
} from "./diagrams";

const info = {
  id: "a".repeat(32),
  name: "図",
  folder_id: null,
  revision_id: "r1",
  revision_number: 1,
  sha256: "abc",
  size: 10,
  etag: '"r-r1"',
  created: 1,
  updated: 2,
  draft: null,
  operation_id: "op",
};
const draft = {
  id: "d1",
  version: 1,
  base_revision: "r1",
  etag: '"d-d1-1"',
  updated: 3,
};

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function reply(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) {
  fetchMock.mockResolvedValueOnce(
    new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status,
      headers,
    }),
  );
}
const call = (n = 0) => {
  const [url, init] = fetchMock.mock.calls[n] as [string, RequestInit];
  return { url, init, headers: init.headers as Record<string, string> };
};

test("creates with the required preconditions and a caller-owned key", async () => {
  reply(info, 201, { ETag: '"r-r1"' });
  const result = await createDiagram(
    info.id,
    { name: "図", folder_id: null, scene: { type: "excalidraw" } },
    "key-1",
  );
  const { url, init, headers } = call();
  expect(url).toBe(`/api/diagrams/${info.id}`);
  expect(init.method).toBe("PUT");
  expect(headers["If-None-Match"]).toBe("*");
  expect(headers["Idempotency-Key"]).toBe("key-1");
  expect(result.info.revision_number).toBe(1);
  expect(result.etag).toBe('"r-r1"');
});

test("saves a revision with If-Match, the raw scene bytes and the same key on retry", async () => {
  const scene = '{"type":"excalidraw","elements":[]}';
  reply({ detail: "x" }, 503);
  await expect(
    saveRevision(info.id, scene, '"r-r1"', "key-2"),
  ).rejects.toMatchObject({ status: 503 });
  reply(info, 201);
  await saveRevision(info.id, scene, '"r-r1"', "key-2");
  for (const n of [0, 1]) {
    const { url, init, headers } = call(n);
    expect(url).toBe(`/api/diagrams/${info.id}/revisions`);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(scene);
    expect(headers["Content-Type"]).toBe("application/vnd.excalidraw+json");
    expect(headers["If-Match"]).toBe('"r-r1"');
    expect(headers["Idempotency-Key"]).toBe("key-2");
  }
});

test("maps failures to DiagramRequestError with the Japanese detail", async () => {
  reply({ detail: "他の場所で更新されました。" }, 412);
  const error = await getDiagram(info.id).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(DiagramRequestError);
  expect(error).toMatchObject({
    status: 412,
    detail: "他の場所で更新されました。",
  });

  reply({ detail: [{ msg: "Value error, 不正です" }] }, 422);
  await expect(getDiagram(info.id)).rejects.toMatchObject({
    status: 422,
    detail: "不正です",
  });

  reply("oops", 500);
  await expect(getDiagram(info.id)).rejects.toMatchObject({
    detail: "HTTP 500",
  });
});

test("reports a missing response as status 0 and keeps aborts as they are", async () => {
  fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
  await expect(getDiagram(info.id)).rejects.toMatchObject({ status: 0 });
  fetchMock.mockRejectedValueOnce(new DOMException("x", "AbortError"));
  await expect(getDiagram(info.id)).rejects.toMatchObject({
    name: "AbortError",
  });
});

test("rejects malformed bodies", async () => {
  reply({ id: "x" });
  await expect(getDiagram(info.id)).rejects.toMatchObject({
    detail: "応答の形式が正しくありません。",
  });
});

test("reads content and builds the download URL", async () => {
  reply('{"type":"excalidraw"}', 200, { ETag: '"r-r1"' });
  const content = await getContent(info.id, "r1");
  expect(call().url).toBe(`/api/diagrams/${info.id}/content?revision=r1`);
  expect(content).toEqual({ scene: '{"type":"excalidraw"}', etag: '"r-r1"' });
  expect(diagramContentUrl(info.id, { download: true })).toBe(
    `/api/diagrams/${info.id}/content?download=true`,
  );
  expect(diagramContentUrl(info.id)).toBe(`/api/diagrams/${info.id}/content`);
});

test("draft operations carry their own ETags", async () => {
  reply(draft, 201, { ETag: draft.etag });
  const created = await createDraft(info.id, null, '"r-r1"');
  expect(call().init.body).toBeUndefined();
  expect(call().headers["If-Match"]).toBe('"r-r1"');
  expect(created.etag).toBe(draft.etag);

  reply({ ...draft, version: 2, etag: '"d-d1-2"' }, 200, {
    ETag: '"d-d1-2"',
  });
  const updated = await updateDraft(info.id, "d1", "{}", draft.etag);
  expect(call(1).init.method).toBe("PUT");
  expect(call(1).headers["If-Match"]).toBe(draft.etag);
  expect(updated.draft.version).toBe(2);

  reply(info, 201);
  await commitDraft(
    info.id,
    "d1",
    { source: '"r-r1"', draft: '"d-d1-2"' },
    "key-3",
  );
  expect(call(2).url).toBe(`/api/diagrams/${info.id}/drafts/d1/commit`);
  expect(call(2).headers["If-Match"]).toBe('"d-d1-2"');
  expect(call(2).headers["Idempotency-Key"]).toBe("key-3");
  expect(JSON.parse(call(2).init.body as string)).toEqual({
    source_etag: '"r-r1"',
  });

  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await discardDraft(info.id, "d1", '"d-d1-2"');
  expect(call(3).init.method).toBe("DELETE");
  expect(call(3).headers["If-Match"]).toBe('"d-d1-2"');
});

test("lists revisions and restores one", async () => {
  const revision = {
    id: "r1",
    number: 1,
    parent: null,
    sha256: "x",
    size: 1,
    created: 1,
    restored_from: null,
    current: true,
  };
  reply({ items: [revision], next: 5 });
  const page = await listRevisions(info.id, { before: 9, limit: 20 });
  expect(call().url).toBe(
    `/api/diagrams/${info.id}/revisions?before=9&limit=20`,
  );
  expect(page).toEqual({ items: [revision], next: 5 });

  reply(info, 201);
  await restoreRevision(info.id, "r0", '"r-r1"', "key-4");
  expect(JSON.parse(call(1).init.body as string)).toEqual({
    revision_id: "r0",
  });
  expect(call(1).headers["Idempotency-Key"]).toBe("key-4");
});

test("finds an existing draft id in a 409", async () => {
  expect(
    existingDraftId(
      new DiagramRequestError(409, "下書きがあります", {
        detail: { draft_id: "dd" },
      }),
    ),
  ).toBe("dd");
  expect(
    existingDraftId(
      new DiagramRequestError(409, `下書き ${"b".repeat(32)} があります`),
    ),
  ).toBe("b".repeat(32));
  expect(existingDraftId(new DiagramRequestError(412, "x"))).toBeNull();
});

test("sha256Hex hashes the UTF-8 bytes like the server does", async () => {
  expect(await sha256Hex("abc")).toBe(
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
  vi.stubGlobal("crypto", {});
  expect(await sha256Hex("abc")).toBeNull();
});

test("a 401 hands the body to the shared login redirect", async () => {
  const body = { login_url: "/login", detail: "ログインが必要です。" };
  reply(body, 401);
  await expect(getDiagram(info.id)).rejects.toMatchObject({
    status: 401,
    detail: "ログインが必要です。",
  });
  expect(login.leaveForLogin).toHaveBeenCalledWith(body);
  login.leaveForLogin.mockClear();
  reply({ detail: "x" }, 403);
  await expect(getDiagram(info.id)).rejects.toMatchObject({ status: 403 });
  expect(login.leaveForLogin).not.toHaveBeenCalled();
});
