import { afterEach, expect, it, vi } from "vitest";
import { uploadFolder } from "./upload-folder";

afterEach(() => vi.unstubAllGlobals());
const folder = (id: string, parent_id: string | null = null) => ({
  id,
  name: "upload",
  parent_id,
  created: 1,
});
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

it("shares concurrent creation and uses the root folder rather than a nested namesake", async () => {
  const fetcher = vi.fn(async (url: string) =>
    url === "/api/library"
      ? response({ folders: [folder("nested", "parent")], jobs: [] })
      : response(folder("root-upload"), 201),
  );
  vi.stubGlobal("fetch", fetcher);
  expect(
    await Promise.all([uploadFolder(), uploadFolder(), uploadFolder()]),
  ).toEqual(["root-upload", "root-upload", "root-upload"]);
  expect(
    fetcher.mock.calls.filter(([url]) => url === "/api/folders"),
  ).toHaveLength(1);
});

it("resolves another tab's concurrent creation instead of creating a second folder", async () => {
  let reads = 0;
  const fetcher = vi.fn(async (url: string) =>
    url === "/api/library"
      ? response({ folders: reads++ ? [folder("other-tab")] : [], jobs: [] })
      : response({ detail: "同じ名前の項目があります。" }, 409),
  );
  vi.stubGlobal("fetch", fetcher);
  expect(await uploadFolder()).toBe("other-tab");
  expect(reads).toBe(2);
});

it("does not retain an old folder id after it is deleted or moved", async () => {
  let current = "first";
  const fetcher = vi.fn(async () =>
    response({ folders: [folder(current)], jobs: [] }),
  );
  vi.stubGlobal("fetch", fetcher);
  expect(await uploadFolder()).toBe("first");
  current = "replacement";
  expect(await uploadFolder()).toBe("replacement");
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("reports a folder creation failure and allows a later retry", async () => {
  let blocked = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/library"
        ? response({ folders: blocked ? [] : [folder("available")], jobs: [] })
        : response({ detail: "フォルダーを保存できません。" }, 500),
    ),
  );
  await expect(uploadFolder()).rejects.toThrow("フォルダーを保存できません。");
  blocked = false;
  await expect(uploadFolder()).resolves.toBe("available");
});
