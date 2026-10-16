import { afterEach, describe, expect, spyOn, test } from "bun:test";
import { randomUUID } from "node:crypto";
import * as fs from "node:fs/promises";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createApi } from "../src/app";
import { clientSchema } from "../src/auth";
import type { Extraction } from "../src/contracts";
import { applyEdits, sha256 } from "../src/editing";
import { responseSchemas } from "../src/responses";
import { Store } from "../src/store";

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});
async function fixture(embedding?: import("../src/providers").EmbeddingProvider) {
  const root = await mkdtemp(join(tmpdir(), "document-editing-"));
  roots.push(root);
  const store = new Store(join(root, "knowledge.sqlite"));
  let clients = [
    clientSchema.parse({
      id: "test",
      token: "a".repeat(40),
      issuer: "test",
      keys: {},
      mode: "batch",
      actions: ["read", "write"],
      scopes: [{ collection_id: "test" }, { collection_id: "other" }],
    }),
  ];
  let calls = 0;
  let pause: (() => Promise<void>) | undefined;
  const api = createApi({
    store,
    embedding,
    artifactRoot: root,
    clients: () => clients,
    extractor: {
      validate: async () => {},
      ready: async () => false,
      extract: async (job, revision): Promise<Extraction> => {
        calls++;
        await pause?.();
        const body = await readFile(
          join(root, "sources", revision.source_id, revision.id, "original.md"),
          "utf8",
        );
        const record = {
          id: randomUUID(),
          text: body,
          kind: "paragraph",
          source_sha256: revision.sha256,
          headings: [],
          refs: [],
          context_refs: [],
          pages: [],
          provenance: [],
          relations: [],
          row_range: [],
          tables: [],
        };
        return {
          source_id: revision.source_id,
          source_revision: revision.id,
          job_id: job.id,
          run: job.run,
          source_sha256: revision.sha256,
          profile: "local-v1",
          contexts: [record],
          chunks: [{ ...record, id: randomUUID(), parent_id: record.id }],
          warnings: [],
        };
      },
    },
  });
  const request = async (
    path: string,
    method = "GET",
    body?: unknown,
    headers: Record<string, string> = {},
  ) =>
    api.app.request(path, {
      method,
      headers: {
        authorization: "Bearer " + clients[0]!.token,
        "content-type": "application/json",
        ...headers,
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  const json = async (
    path: string,
    method = "GET",
    body?: unknown,
    headers: Record<string, string> = {},
  ) => {
    const response = await request(path, method, body, headers);
    const data = await response.json();
    if (!response.ok) throw new Error(JSON.stringify(data));
    return data;
  };
  const newDraft = (body = "本文 **強調**", key = randomUUID()) =>
    json(
      "/api/v1/drafts",
      "POST",
      { body, metadata: { title: "文書", collection_id: "test", source_kind: "wiki" } },
      { "idempotency-key": key },
    );
  const commit = (
    d: { draft_id: string; etag: string; base_etag: string | null },
    key = randomUUID(),
  ) =>
    json(
      `/api/v1/drafts/${d.draft_id}/commit`,
      "POST",
      { source_etag: d.base_etag },
      { "if-match": d.etag, "idempotency-key": key },
    );
  return {
    root,
    store,
    api,
    request,
    json,
    newDraft,
    commit,
    calls: () => calls,
    pause: (value: (() => Promise<void>) | undefined) => {
      pause = value;
    },
    setClients: (value: typeof clients) => {
      clients = value;
    },
    clients: () => clients,
  };
}
describe("editor-neutral persistence", () => {
  test("draft autosave avoids extraction, commit retains raw bytes and search is separate", async () => {
    const f = await fixture();
    const raw =
      "\ufeff---\r\ntitle: frontmatter\r\n---\r\n\r\n```mermaid\r\ngraph TD; A-->B\r\n```\r\n\r\n$$x^2$$\r\n\r\n:::custom\r\n本文 😀\r\n:::\r\n![image](knowledge-attachment:opaque)\r\n";
    const d = await f.newDraft(raw);
    expect(f.calls()).toBe(0);
    expect(f.store.outstanding("extract")).toBe(0);
    const save = await f.commit(d);
    expect(save.status.search).toBe("pending");
    expect(f.calls()).toBe(0);
    const body = await f.json(`/api/v1/sources/${save.source_id}/body`);
    expect(body.body).toBe(raw);
    expect(body.sha256).toBe(sha256(raw));
    expect(responseSchemas["get /api/v1/sources/{source_id}/body"]!.safeParse(body).success).toBe(
      true,
    );
    await f.api.worker.tick();
    expect(f.calls()).toBe(1);
    const search = await f.json("/api/v1/search", "POST", {
      query: "本文",
      scope: { collection_ids: ["test"] },
      mode: "hybrid",
    });
    expect(search.results[0].source_revision).toBe(save.source_revision);
    expect(search.effective_mode).toBe("text");
    expect((await f.json(`/api/v1/sources/${save.source_id}/body`)).status.fts_ready).toBe(true);
    f.store.db.close();
  });
  test("human and AI stale draft updates are rejected atomically", async () => {
    const f = await fixture();
    const d = await f.newDraft();
    const path = `/api/v1/drafts/${d.draft_id}`;
    const updated = await f.json(
      path,
      "PUT",
      { body: "人の変更" },
      { "if-match": d.etag, "idempotency-key": randomUUID() },
    );
    expect(
      (
        await f.request(
          path,
          "PUT",
          { body: "AIの変更" },
          { "if-match": d.etag, "idempotency-key": randomUUID() },
        )
      ).status,
    ).toBe(412);
    expect((await f.json(path)).body).toBe("人の変更");
    expect(updated.version).toBe(2);
    f.store.db.close();
  });
  test("two drafts from one source cannot overwrite the first committed revision", async () => {
    const f = await fixture();
    const saved = await f.commit(await f.newDraft());
    const input = {
      source_id: saved.source_id,
      source_revision: saved.source_revision,
      source_etag: saved.source_etag,
    };
    const a = await f.json("/api/v1/drafts", "POST", input, { "idempotency-key": randomUUID() });
    const b = await f.json("/api/v1/drafts", "POST", input, { "idempotency-key": randomUUID() });
    await f.commit(a);
    expect(
      (
        await f.request(
          `/api/v1/drafts/${b.draft_id}/commit`,
          "POST",
          { source_etag: b.base_etag },
          { "if-match": b.etag, "idempotency-key": randomUUID() },
        )
      ).status,
    ).toBe(412);
    f.store.db.close();
  });
  test("replays and lookup survive reopening and cannot duplicate a committed draft", async () => {
    const f = await fixture();
    const key = randomUUID();
    const d = await f.newDraft("再送", key);
    expect((await f.newDraft("再送", key)).draft_id).toBe(d.draft_id);
    const saveKey = randomUUID();
    const saved = await f.commit(d, saveKey);
    expect((await f.commit(d, saveKey)).source_revision).toBe(saved.source_revision);
    expect(
      (
        await f.json("/api/v1/operations/lookup", "POST", {
          operation: "commit",
          target_id: d.draft_id,
          key: saveKey,
        })
      ).result.sha256,
    ).toBe(saved.sha256);
    expect(f.store.db.query("SELECT count(*) AS n FROM revisions").get()).toEqual({ n: 1 });
    f.store.db.close();
    const reopened = new Store(join(f.root, "knowledge.sqlite"));
    expect(reopened.revision(saved.source_revision)?.sha256).toBe(saved.sha256);
    expect(reopened.db.query("SELECT count(*) AS n FROM drafts").get()).toEqual({ n: 1 });
    reopened.db.close();
  });
  test("partial mismatch, multiple matches and invalid UTF8 ranges leave the draft unchanged", async () => {
    const f = await fixture();
    const d = await f.newDraft("😀 同じ 同じ");
    for (const edit of [
      { expected: "なし", replacement: "x" },
      { expected: "同じ", replacement: "x" },
      { start: 1, end: 2, expected: "", replacement: "x" },
    ]) {
      const result = await f.request(
        `/api/v1/drafts/${d.draft_id}`,
        "PATCH",
        { base_sha256: d.sha256, edits: [edit] },
        { "if-match": d.etag, "idempotency-key": randomUUID() },
      );
      expect(result.ok).toBe(false);
      expect((await f.json(`/api/v1/drafts/${d.draft_id}`)).etag).toBe(d.etag);
    }
    const edited = await f.json(
      `/api/v1/drafts/${d.draft_id}`,
      "PATCH",
      { base_sha256: d.sha256, edits: [{ start: 0, end: 4, expected: "😀", replacement: "成功" }] },
      { "if-match": d.etag, "idempotency-key": randomUUID() },
    );
    expect(edited.body).toBe("成功 同じ 同じ");
    f.store.db.close();
  });
  test("strict schemas reject forged identity and read-only or out-of-scope writes", async () => {
    const f = await fixture();
    expect(
      (
        await f.request(
          "/api/v1/drafts",
          "POST",
          { body: "x", user_id: "admin", metadata: { title: "x", collection_id: "test" } },
          { "idempotency-key": randomUUID() },
        )
      ).status,
    ).toBe(422);
    const d = await f.newDraft();
    f.setClients([{ ...f.clients()[0]!, actions: ["read"] }]);
    expect(
      (
        await f.request(
          `/api/v1/drafts/${d.draft_id}`,
          "PUT",
          { body: "x" },
          { "if-match": d.etag, "idempotency-key": randomUUID() },
        )
      ).status,
    ).toBe(403);
    f.setClients([
      { ...f.clients()[0]!, actions: ["read", "write"], scopes: [{ collection_id: "other" }] },
    ]);
    expect((await f.request(`/api/v1/drafts/${d.draft_id}`)).status).toBe(404);
    f.store.db.close();
  });
  test("queue saturation does not roll back Markdown persistence", async () => {
    const f = await fixture();
    for (let i = 0; i < 5; i++) await f.commit(await f.newDraft(`本文 ${i}`));
    expect(f.store.outstanding("extract")).toBe(5);
    expect(f.calls()).toBe(0);
    f.store.db.close();
  });
  test("metadata changes preserve frontmatter and stale bases conflict", async () => {
    const f = await fixture();
    const save = await f.commit(await f.newDraft("---\ntitle: 原文\n---\n本文"));
    const metadata = { title: "APIのタイトル", collection_id: "test", source_kind: "wiki" };
    await f.json(`/api/v1/sources/${save.source_id}/metadata`, "PATCH", metadata, {
      "if-match": save.source_etag,
      "idempotency-key": randomUUID(),
    });
    const body = await f.json(`/api/v1/sources/${save.source_id}/body`);
    expect(body.body).toContain("title: 原文");
    expect(body.metadata.title).toBe("APIのタイトル");
    expect(body.source_revision).toBe(save.source_revision);
    f.store.db.close();
  });
  test("attachment references survive commit; wrong document and deletion cannot expose bytes", async () => {
    const f = await fixture();
    const d = await f.newDraft();
    const a = await f.json(
      `/api/v1/drafts/${d.draft_id}/attachments`,
      "POST",
      {
        filename: "image.png",
        media_type: "image/png",
        body_base64: Buffer.from("synthetic").toString("base64"),
      },
      { "if-match": d.etag, "idempotency-key": randomUUID() },
    );
    const current = await f.json(`/api/v1/drafts/${d.draft_id}`);
    const save = await f.commit(current);
    expect(
      (await f.json(`/api/v1/sources/${save.source_id}/attachments/${a.attachment_id}`))
        .body_base64,
    ).toBe(Buffer.from("synthetic").toString("base64"));
    const other = await f.commit(await f.newDraft());
    expect(
      (await f.request(`/api/v1/sources/${other.source_id}/attachments/${a.attachment_id}`)).status,
    ).toBe(404);
    await f.json(`/api/v1/sources/${save.source_id}`, "DELETE", undefined, {
      "if-match": save.source_etag,
    });
    expect((await f.request(`/api/v1/drafts/${d.draft_id}`)).status).toBe(404);
    expect(
      (await f.request(`/api/v1/sources/${save.source_id}/attachments/${a.attachment_id}`)).status,
    ).toBe(404);
    f.store.db.close();
  });
});
test("edits form one atomic base and reject overlap", () => {
  expect(() =>
    applyEdits("abc", {
      base_sha256: sha256("abc"),
      edits: [
        { start: 0, end: 2, expected: "ab", replacement: "x" },
        { start: 1, end: 3, expected: "bc", replacement: "y" },
      ],
    }),
  ).toThrow("overlapping_edits");
});

test("scope change fences the running job and its old grant cannot read the document", async () => {
  const f = await fixture();
  const saved = await f.commit(await f.newDraft("権限変更中の本文"));
  let release: () => void = () => {};
  let started: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const entered = new Promise<void>((resolve) => {
    started = resolve;
  });
  f.pause(async () => {
    started();
    await gate;
  });
  const running = f.api.worker.tick();
  await entered;
  await f.json(
    `/api/v1/sources/${saved.source_id}/metadata`,
    "PATCH",
    { title: "移動後", collection_id: "other" },
    { "if-match": saved.source_etag, "idempotency-key": randomUUID() },
  );
  release();
  await running;
  expect(f.store.job(saved.job_id)?.state).toBe("superseded");
  expect(f.store.revision(saved.source_revision)?.fts_ready).toBe(0);
  f.pause(undefined);
  await f.api.worker.tick();
  expect(f.store.revision(saved.source_revision)?.fts_ready).toBe(1);
  f.setClients([{ ...f.clients()[0]!, scopes: [{ collection_id: "test" }] }]);
  expect((await f.request(`/api/v1/sources/${saved.source_id}/body`)).status).toBe(404);
  f.store.db.close();
});
test("Embedding failure does not lose the saved body or FTS", async () => {
  const f = await fixture({
    identity: "fixture-failure",
    dimensions: 2,
    embed: async () => {
      throw new Error("fixture unavailable");
    },
  });
  const saved = await f.commit(await f.newDraft("保持する根拠"));
  await f.api.worker.tick();
  const body = await f.json(`/api/v1/sources/${saved.source_id}/body`);
  expect(body.body).toBe("保持する根拠");
  expect(body.status.search).toBe("complete");
  expect(body.status.semantic_ready).toBe(false);
  expect(body.status.error_code).toBe("embedding_failed");
  f.store.db.close();
});
test("incompatible vectors are excluded and reindex rebuilds without extraction", async () => {
  let count = 0;
  const provider = {
    identity: "fixture-version-1",
    dimensions: 2,
    embed: async (texts: string[]) => {
      count++;
      return texts.map(() => [1, 2]);
    },
  };
  const f = await fixture(provider);
  const saved = await f.commit(await f.newDraft("索引の根拠"));
  await f.api.worker.tick();
  expect((await f.json(`/api/v1/sources/${saved.source_id}/body`)).status.semantic_ready).toBe(
    true,
  );
  provider.identity = "fixture-version-2";
  expect((await f.json(`/api/v1/sources/${saved.source_id}`)).semantic_ready).toBe(false);
  expect((await f.json(`/api/v1/sources/${saved.source_id}/body`)).status.semantic_ready).toBe(
    false,
  );
  const search = await f.json("/api/v1/search", "POST", {
    query: "根拠",
    scope: { collection_ids: ["test"] },
    mode: "hybrid",
  });
  expect(search.degraded_reasons).toContain("embedding_not_ready");
  await f.json(
    `/api/v1/sources/${saved.source_id}/reindex`,
    "POST",
    {},
    { "if-match": saved.source_etag },
  );
  await f.api.worker.tick();
  expect(f.calls()).toBe(1);
  expect(count).toBeGreaterThan(1);
  expect((await f.json(`/api/v1/sources/${saved.source_id}/body`)).status.semantic_ready).toBe(
    true,
  );
  f.store.db.close();
});
test("an empty committed Markdown has no evidence candidates, not an extraction failure", async () => {
  const f = await fixture();
  const saved = await f.commit(await f.newDraft(""));
  await f.api.worker.tick();
  const body = await f.json(`/api/v1/sources/${saved.source_id}/body`);
  expect(body.body).toBe("");
  expect(body.status.fts_ready).toBe(true);
  expect(f.calls()).toBe(0);
  const reference = {
    source_id: saved.source_id,
    source_revision: saved.source_revision,
    evidence_revision: body.evidence_revision,
  };
  const outline = await f.request("/api/v1/document-reads/outline", "POST", reference);
  expect(outline.status).toBe(200);
  expect(await outline.json()).toMatchObject({
    ...reference,
    kind: "document",
    entries: [],
    units: [],
    total: 0,
    next_offset: null,
  });
  const tables = await f.json("/api/v1/document-reads/tables", "POST", reference);
  expect(tables.total).toBe(0);
  expect(
    (await f.request("/api/v1/document-reads/page-image", "POST", { ...reference, unit: 1 }))
      .status,
  ).toBe(422);
  expect(
    (
      await f.request("/api/v1/document-reads/section", "POST", {
        ...reference,
        selector: { kind: "elements", start_ref: "#/texts/0", end_ref: "#/texts/0" },
      })
    ).status,
  ).toBe(422);
  expect(
    (
      await f.request("/api/v1/document-reads/table", "POST", {
        ...reference,
        table_ref: "#/tables/0",
      })
    ).status,
  ).toBe(404);
  expect(f.calls()).toBe(0);
  f.store.db.close();
});
test("attachment bindings survive a subsequent draft and revision", async () => {
  const f = await fixture();
  const d = await f.newDraft("画像参照");
  const a = await f.json(
    `/api/v1/drafts/${d.draft_id}/attachments`,
    "POST",
    {
      filename: "image.png",
      media_type: "image/png",
      body_base64: Buffer.from("opaque").toString("base64"),
    },
    { "if-match": d.etag, "idempotency-key": randomUUID() },
  );
  const saved = await f.commit(await f.json(`/api/v1/drafts/${d.draft_id}`));
  const next = await f.json(
    "/api/v1/drafts",
    "POST",
    {
      source_id: saved.source_id,
      source_revision: saved.source_revision,
      source_etag: saved.source_etag,
    },
    { "idempotency-key": randomUUID() },
  );
  expect(next.attachments).toEqual([a.attachment_id]);
  expect(
    (await f.json(`/api/v1/drafts/${next.draft_id}/attachments/${a.attachment_id}`)).body_base64,
  ).toBe(Buffer.from("opaque").toString("base64"));
  const second = await f.commit(next);
  expect(
    (await f.json(`/api/v1/sources/${second.source_id}/attachments`)).attachments[0].attachment_id,
  ).toBe(a.attachment_id);
  f.store.db.close();
});

test("oversized draft responses reject before persisting a mutation", async () => {
  const f = await fixture();
  const d = await f.newDraft("small");
  const before = f.store.db.query("SELECT count(*) n FROM idempotency").get();
  // Fits the request envelope but cannot fit the returned draft and lookup envelope.
  const response = await f.request(
    `/api/v1/drafts/${d.draft_id}`,
    "PUT",
    { body: "x".repeat(2 * 1024 * 1024 - 100) },
    { "if-match": d.etag, "idempotency-key": "oversized-result" },
  );
  expect(response.status).toBe(413);
  const current = await f.json(`/api/v1/drafts/${d.draft_id}`);
  expect(current.body).toBe("small");
  expect(current.etag).toBe(d.etag);
  expect(f.store.db.query("SELECT count(*) n FROM idempotency").get()).toEqual(before);
  f.store.db.close();
});

test("attachment retrieval rejects content that differs from its immutable hash", async () => {
  const f = await fixture();
  const d = await f.newDraft();
  const a = await f.json(
    `/api/v1/drafts/${d.draft_id}/attachments`,
    "POST",
    {
      filename: "a.png",
      media_type: "image/png",
      body_base64: Buffer.from("original").toString("base64"),
    },
    { "if-match": d.etag, "idempotency-key": "attach-integrity" },
  );
  await Bun.write(
    join(f.root, "sources", d.source_id, "attachments", a.attachment_id, "original"),
    "corrupt",
  );
  expect(
    (await f.request(`/api/v1/drafts/${d.draft_id}/attachments/${a.attachment_id}`)).status,
  ).toBe(500);
  const saved = await f.commit(await f.json(`/api/v1/drafts/${d.draft_id}`));
  expect(
    (await f.request(`/api/v1/sources/${saved.source_id}/attachments/${a.attachment_id}/content`))
      .status,
  ).toBe(500);
  f.store.db.close();
});

test("a scope move during retrieval cannot return a result outside the requested scope", async () => {
  let wait: (() => Promise<void>) | undefined;
  const f = await fixture({
    identity: "fixture",
    dimensions: 2,
    embed: async (texts) => {
      await wait?.();
      return texts.map(() => [1, 2]);
    },
  });
  const saved = await f.commit(await f.newDraft("検索範囲の根拠"));
  await f.api.worker.tick();
  const input = { query: "根拠", scope: { collection_ids: ["test"] }, mode: "hybrid" };
  const old = await f.json("/api/v1/search", "POST", input);
  let entered: () => void = () => {};
  let release: () => void = () => {};
  const started = new Promise<void>((r) => {
    entered = r;
  });
  const gate = new Promise<void>((r) => {
    release = r;
  });
  wait = async () => {
    entered();
    await gate;
  };
  const searching = f.json("/api/v1/search", "POST", input);
  await started;
  await f.json(
    `/api/v1/sources/${saved.source_id}/metadata`,
    "PATCH",
    { title: "Moved", collection_id: "other" },
    { "if-match": saved.source_etag, "idempotency-key": "move-during-search" },
  );
  release();
  const after = await searching;
  expect(after.results).toHaveLength(0);
  // The actor has both grants; the retrieval itself was confined to test.
  const reference = old.results[0];
  expect(
    (
      await f.request("/api/v1/context", "POST", {
        retrieval_id: old.retrieval_id,
        references: [
          {
            source_id: reference.source_id,
            source_revision: reference.source_revision,
            evidence_revision: reference.evidence_revision,
            context_id: reference.context_id,
          },
        ],
      })
    ).status,
  ).toBe(404);
  f.store.db.close();
});

test("metadata edits and draft commits retain external-key uniqueness", async () => {
  const f = await fixture();
  const create = (key: string) =>
    f.json(
      "/api/v1/markdown-sources",
      "POST",
      { body: key, metadata: { title: key, collection_id: "test", external_key: key } },
      { "idempotency-key": key },
    );
  await create("first");
  const second = await create("second");
  const conflict = { title: "Second", collection_id: "test", external_key: "first" };
  expect(
    (
      await f.request(`/api/v1/sources/${second.source_id}/metadata`, "PATCH", conflict, {
        "if-match": second.source_etag,
        "idempotency-key": "duplicate-metadata",
      })
    ).status,
  ).toBe(409);
  const draft = await f.json(
    "/api/v1/drafts",
    "POST",
    {
      source_id: second.source_id,
      source_revision: second.source_revision,
      source_etag: second.source_etag,
      metadata: conflict,
    },
    { "idempotency-key": "duplicate-draft" },
  );
  expect(
    (
      await f.request(
        `/api/v1/drafts/${draft.draft_id}/commit`,
        "POST",
        { source_etag: draft.base_etag },
        { "if-match": draft.etag, "idempotency-key": "duplicate-commit" },
      )
    ).status,
  ).toBe(409);
  expect(f.store.db.query("SELECT count(*) n FROM revisions").get()).toEqual({ n: 2 });
  expect((await f.json(`/api/v1/sources/${second.source_id}/body`)).metadata.external_key).toBe(
    "second",
  );
  f.store.db.close();
});

test("Markdown creation rejects a body that cannot be retrieved as a public response", async () => {
  const f = await fixture();
  const response = await f.request(
    "/api/v1/markdown-sources",
    "POST",
    {
      body: "x".repeat(2 * 1024 * 1024 - 200),
      metadata: { title: "x", collection_id: "test" },
    },
    { "idempotency-key": "oversized-canonical" },
  );
  expect(response.status).toBe(413);
  expect(f.store.db.query("SELECT count(*) n FROM sources").get()).toEqual({ n: 0 });
  expect(f.store.db.query("SELECT count(*) n FROM idempotency").get()).toEqual({ n: 0 });
  f.store.db.close();
});

test("attachment lists reject duplicates and stop at the published limit", async () => {
  const f = await fixture();
  const initial = await f.newDraft();
  const first = await f.json(
    `/api/v1/drafts/${initial.draft_id}/attachments`,
    "POST",
    {
      filename: "a",
      media_type: "application/octet-stream",
      body_base64: "eA==",
    },
    { "if-match": initial.etag, "idempotency-key": "first-limit" },
  );
  // Seed a full registered collection without exceeding the separate API rate
  // limit. The boundary requests below still use the public HTTP operations.
  const ids = [first.attachment_id];
  for (let i = 1; i < 100; i++) {
    const aid = randomUUID();
    f.store.db.run("INSERT INTO attachments VALUES(?,?,?,?,?,?,?)", [
      aid,
      initial.source_id,
      initial.draft_id,
      "a",
      "application/octet-stream",
      1,
      first.sha256,
    ]);
    ids.push(aid);
  }
  f.store.db.run("UPDATE drafts SET attachments=? WHERE id=?", [
    JSON.stringify(ids),
    initial.draft_id,
  ]);
  const d = await f.json(`/api/v1/drafts/${initial.draft_id}`);
  expect(
    (
      await f.request(
        `/api/v1/drafts/${d.draft_id}/attachments`,
        "POST",
        {
          filename: "a",
          media_type: "application/octet-stream",
          body_base64: "eA==",
        },
        { "if-match": d.etag, "idempotency-key": "over-limit" },
      )
    ).status,
  ).toBe(422);
  expect(
    (
      await f.request(
        `/api/v1/drafts/${d.draft_id}`,
        "PUT",
        {
          body: d.body,
          attachments: [first.attachment_id, first.attachment_id],
        },
        { "if-match": d.etag, "idempotency-key": "duplicates" },
      )
    ).status,
  ).toBe(422);
  expect((await f.json(`/api/v1/drafts/${d.draft_id}`)).etag).toBe(d.etag);
  expect(f.store.db.query("SELECT count(*) n FROM attachments").get()).toEqual({ n: 100 });
  f.store.db.close();
});

test("a non-empty original truncated on disk cannot be indexed as an empty success", async () => {
  const f = await fixture();
  const saved = await f.commit(await f.newDraft("canonical"));
  await Bun.write(
    join(f.root, "sources", saved.source_id, saved.source_revision, "original.md"),
    "",
  );
  await f.api.worker.tick();
  expect(f.store.job(saved.job_id)?.state).toBe("failed");
  expect(f.store.job(saved.job_id)?.error_code).toBe("source_integrity_failed");
  expect(f.store.revision(saved.source_revision)?.fts_ready).toBe(0);
  expect(f.calls()).toBe(0);
  f.store.db.close();
});

test("the Markdown viewer resolves only current bound attachments through its session", async () => {
  const f = await fixture();
  const d = await f.newDraft("<script>danger()</script>");
  const a = await f.json(
    `/api/v1/drafts/${d.draft_id}/attachments`,
    "POST",
    {
      filename: "<unsafe>.png",
      media_type: "image/png",
      body_base64: "eA==",
    },
    { "if-match": d.etag, "idempotency-key": "viewer-image" },
  );
  const saved = await f.commit(await f.json(`/api/v1/drafts/${d.draft_id}`));
  await f.api.worker.tick();
  const data = await f.json(`/api/v1/sources/${saved.source_id}/body`);
  const ref = {
    source_id: data.source_id,
    source_revision: data.source_revision,
    evidence_revision: data.evidence_revision,
    prefix: `/viewer/session/${"a".repeat(43)}/`,
  };
  const view = await f.json("/api/v1/viewer", "POST", { ...ref, resource: "view/document" });
  const html = Buffer.from(view.body_base64, "base64").toString();
  expect(html).toContain(`src="${ref.prefix}attachment/${a.attachment_id}"`);
  expect(html).toContain("&lt;script&gt;");
  expect(html).toContain("&lt;unsafe&gt;");
  const image = await f.json("/api/v1/viewer", "POST", {
    ...ref,
    resource: `attachment/${a.attachment_id}`,
  });
  expect(image.media_type).toBe("image/png");
  expect(image.body_base64).toBe("eA==");
  expect(
    (await f.request("/api/v1/viewer", "POST", { ...ref, resource: `attachment/${randomUUID()}` }))
      .status,
  ).toBe(404);
  await f.json(`/api/v1/sources/${saved.source_id}`, "DELETE", undefined, {
    "if-match": saved.source_etag,
  });
  expect(
    (
      await f.request("/api/v1/viewer", "POST", {
        ...ref,
        resource: `attachment/${a.attachment_id}`,
      })
    ).status,
  ).toBe(404);
  f.store.db.close();
});

test("attachments reject insufficient storage without mutation and replay without new space", async () => {
  const f = await fixture();
  try {
    const d = await f.newDraft();
    const path = `/api/v1/drafts/${d.draft_id}/attachments`;
    const input = {
      filename: "image.bin",
      media_type: "application/octet-stream",
      body_base64: Buffer.from("synthetic").toString("base64"),
    };
    const headers = { "if-match": d.etag, "idempotency-key": "attachment" };
    const disk = spyOn(fs, "statfs");
    try {
      disk.mockResolvedValue({ bavail: 0, bsize: 4096 } as Awaited<ReturnType<typeof fs.statfs>>);
      expect((await f.request(path, "POST", input, headers)).status).toBe(507);
      expect((await f.json(`/api/v1/drafts/${d.draft_id}`)).attachments).toEqual([]);
      expect(f.store.db.query("SELECT * FROM attachments").all()).toEqual([]);
      expect(await fs.readdir(f.root)).not.toContain("sources");
      disk.mockRestore();
      const saved = await f.json(path, "POST", input, headers);
      const low = spyOn(fs, "statfs").mockResolvedValue({ bavail: 0, bsize: 4096 } as Awaited<
        ReturnType<typeof fs.statfs>
      >);
      try {
        expect((await f.json(path, "POST", input, headers)).attachment_id).toBe(
          saved.attachment_id,
        );
        expect(low).not.toHaveBeenCalled();
      } finally {
        low.mockRestore();
      }
    } finally {
      disk.mockRestore();
    }
  } finally {
    f.store.db.close();
  }
});

test("source and attachment requests share admission before buffering and release on failure", async () => {
  const f = await fixture();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let entered = 0;
  let started!: () => void;
  const ready = new Promise<void>((resolve) => {
    started = resolve;
  });
  const pending: Promise<Response>[] = [];
  const send = async (path: string, bytes: Uint8Array, contentType: string, etag?: string) => {
    const body = new ReadableStream<Uint8Array>(
      {
        async pull(controller) {
          entered++;
          if (entered === 3) started();
          await gate;
          controller.enqueue(bytes);
          controller.close();
        },
      },
      { highWaterMark: 0 },
    );
    return f.api.app.request(path, {
      method: "POST",
      headers: {
        authorization: `Bearer ${f.clients()[0]?.token}`,
        "content-type": contentType,
        "idempotency-key": randomUUID(),
        ...(etag ? { "if-match": etag } : {}),
      },
      body,
    });
  };
  try {
    const drafts = await Promise.all([f.newDraft("a"), f.newDraft("b"), f.newDraft("c")]);
    for (const d of drafts.slice(0, 2)) {
      pending.push(
        send(
          `/api/v1/drafts/${d.draft_id}/attachments`,
          Buffer.from("{invalid json"),
          "application/json",
          d.etag,
        ),
      );
    }
    const contentType = "multipart/form-data; boundary=review-boundary";
    const bytes = Buffer.from(
      '--review-boundary\r\nContent-Disposition: form-data; name="file"; filename="review.txt"\r\n\r\nsynthetic source\r\n' +
        '--review-boundary\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n' +
        JSON.stringify({ title: "Review", collection_id: "test", language: "ja" }) +
        "\r\n--review-boundary--\r\n",
    );
    pending.push(send("/api/v1/sources", bytes, contentType));
    await ready;
    const d = drafts[2];
    const refused = await send(
      `/api/v1/drafts/${d.draft_id}/attachments`,
      Buffer.from("{}"),
      "application/json",
      d.etag,
    );
    expect(refused.status).toBe(429);
    expect((await send("/api/v1/sources", bytes, contentType)).status).toBe(429);
    expect(entered).toBe(3);
    release();
    expect((await Promise.all(pending)).map((r) => r.status)).toEqual([422, 422, 202]);
    // Invalid input and successful source registration both return their shared slots.
    const input = {
      filename: "x.bin",
      media_type: "application/octet-stream",
      body_base64: Buffer.from("safe").toString("base64"),
    };
    expect(
      (
        await f.request(`/api/v1/drafts/${d.draft_id}/attachments`, "POST", input, {
          "if-match": d.etag,
          "idempotency-key": randomUUID(),
        })
      ).status,
    ).toBe(200);
  } finally {
    release();
    await Promise.allSettled(pending);
    f.store.db.close();
  }
});
