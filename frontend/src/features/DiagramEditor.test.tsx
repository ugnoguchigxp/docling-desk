import { useLayoutEffect, useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { DiagramInfo, RevisionInfo } from "../lib/diagrams";
import type { Job } from "../lib/types";

const api = vi.hoisted(() => ({
  getDiagram: vi.fn(),
  getContent: vi.fn(),
  getDraft: vi.fn(),
  getDraftContent: vi.fn(),
  saveRevision: vi.fn(),
  createDraft: vi.fn(),
  updateDraft: vi.fn(),
  commitDraft: vi.fn(),
  discardDraft: vi.fn(),
  listRevisions: vi.fn(),
  restoreRevision: vi.fn(),
}));
const exporter = vi.hoisted(() => ({
  exportSvg: vi.fn(),
  exportRaster: vi.fn(),
  exportHtml: vi.fn(),
}));
const edits = vi.hoisted(() => ({ n: 0 }));
// Autosave timing is under the test's control: unless a test asks for the real
// values (and then drives fake timers), no product timer can ever fire.
const NEVER = 3_600_000;
const timing = vi.hoisted(() => ({ idle: 3_600_000, interval: 3_600_000 }));

vi.mock("../diagram/draft-timing", () => ({
  get DRAFT_IDLE_MS() {
    return timing.idle;
  },
  get DRAFT_INTERVAL_MS() {
    return timing.interval;
  },
}));

vi.mock("../lib/diagrams", async (original) => ({
  ...(await original<typeof import("../lib/diagrams")>()),
  ...api,
}));
vi.mock("../diagram/export", () => exporter);
vi.mock("../diagram/DiagramCanvas", () => {
  function Canvas(props: {
    initialScene: string | null;
    onChange: (scene: string) => void;
  }) {
    // A layout effect: the initial report must precede anything a test does
    // once the canvas is in the DOM (a passive effect may still be pending).
    const [broken, setBroken] = useState(false);
    // A runtime render error of the canvas, which the feature boundary catches.
    if (broken) throw new Error("canvas render error");
    useLayoutEffect(() => {
      // Excalidraw reports its restored initial state once after mount.
      props.onChange(`norm:${props.initialScene}`);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid="canvas" data-scene={props.initialScene}>
        <button onClick={() => props.onChange(`edit-${++edits.n}`)}>
          edit
        </button>
        <button onClick={() => setBroken(true)}>crash</button>
      </div>
    );
  }
  return { default: Canvas };
});

import { DiagramEditor } from "./DiagramEditor";
import { HttpError } from "../lib/http";

const REAL = await vi.importActual<typeof import("../diagram/draft-timing")>(
  "../diagram/draft-timing",
);
const DRAFT_IDLE_MS = REAL.DRAFT_IDLE_MS;
const DRAFT_INTERVAL_MS = REAL.DRAFT_INTERVAL_MS;
/** Autosave tests: use the product's timing, and drive it with fake timers. */
const realTiming = () => {
  timing.idle = DRAFT_IDLE_MS;
  timing.interval = DRAFT_INTERVAL_MS;
};

const job = { id: "d".repeat(32), filename: "図.excalidraw" } as Job;
const info = (patch: Partial<DiagramInfo> = {}): DiagramInfo => ({
  id: job.id,
  name: "図.excalidraw",
  folder_id: null,
  revision_id: "r1",
  revision_number: 1,
  sha256: "x",
  size: 1,
  etag: '"r-r1"',
  created: 1,
  updated: 2,
  draft: null,
  operation_id: null,
  ...patch,
});
const revision = (n: number, current = false): RevisionInfo => ({
  id: `r${n}`,
  number: n,
  parent: n > 1 ? `r${n - 1}` : null,
  sha256: "x",
  size: 1,
  created: n,
  restored_from: null,
  current,
});

beforeEach(() => {
  edits.n = 0;
  timing.idle = NEVER;
  timing.interval = NEVER;
  api.getDiagram.mockResolvedValue({ info: info(), etag: '"r-r1"' });
  api.getContent.mockResolvedValue({ scene: "scene-1", etag: '"r-r1"' });
  api.listRevisions.mockResolvedValue({
    items: [revision(2, true), revision(1)],
    next: null,
  });
  api.discardDraft.mockResolvedValue(undefined);
  vi.stubGlobal("crypto", {
    randomUUID: vi.fn(() => `key-${Math.random()}`),
    subtle: webcrypto.subtle,
  });
});
afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  // Nothing may still be talking to the server once the editor is gone.
  const calls = (): Record<string, number> =>
    Object.fromEntries(
      Object.entries(api).map(([name, fn]) => [name, fn.mock.calls.length]),
    );
  const atUnmount = calls();
  await new Promise((resolve) => setTimeout(resolve, 25));
  expect(calls()).toEqual(atUnmount);
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  for (const fn of [...Object.values(api), ...Object.values(exporter)])
    fn.mockReset();
});

async function open(onChanged?: () => void) {
  const view = render(<DiagramEditor job={job} onChanged={onChanged} />);
  await screen.findByTestId("canvas");
  return view;
}
const edit = () =>
  fireEvent.click(screen.getByRole("button", { name: "edit" }));
const saveButton = () => screen.getByRole("button", { name: "保存" });
const saved = (n = 2): { info: DiagramInfo; etag: string } => ({
  info: info({ revision_id: `r${n}`, revision_number: n, etag: `"r-r${n}"` }),
  etag: `"r-r${n}"`,
});

test("opens the current revision with the original download link", async () => {
  await open();
  expect(api.getContent).toHaveBeenCalledWith(job.id, "r1", expect.anything());
  expect(screen.getByTestId("canvas")).toHaveAttribute("data-scene", "scene-1");
  expect(screen.getByText(/保存済み/)).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
  expect(document.getElementById("originalDownload")).toHaveAttribute(
    "href",
    `/api/diagrams/${job.id}/content?download=true`,
  );
  // "原本を保存" fetches the saved version; "保存" makes a new one. Each says so.
  expect(document.getElementById("originalDownload")).toHaveAttribute(
    "title",
    "保存済みの最新版（版 1）を取得します。保存していない変更は含まれません。",
  );
  expect(saveButton()).toHaveAttribute(
    "title",
    "保存していない変更はありません",
  );
  expect(saveButton()).toHaveClass("primary");
});

test("reports a load failure and can retry", async () => {
  api.getDiagram.mockRejectedValueOnce(new HttpError(500, "壊れています"));
  render(<DiagramEditor job={job} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("壊れています");
  fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
  await screen.findByTestId("canvas");
});

test("saves a revision with the base ETag and reports the change", async () => {
  const onChanged = vi.fn();
  api.saveRevision.mockResolvedValue(saved());
  await open(onChanged);
  edit();
  expect(screen.getByText(/未保存の変更があります/)).toHaveAttribute(
    "data-kind",
    "unsaved",
  );
  fireEvent.click(saveButton());
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(api.saveRevision).toHaveBeenCalledWith(
    job.id,
    "edit-1",
    '"r-r1"',
    expect.any(String),
  );
  expect(screen.getByText(/保存済み · 版 2/)).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
});

test("keeps the input and reuses the idempotency key when a save is retried", async () => {
  api.saveRevision
    .mockRejectedValueOnce(new HttpError(0, "接続できません"))
    .mockResolvedValueOnce(saved());
  await open();
  edit();
  fireEvent.click(saveButton());
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("通信できませんでした");
  expect(alert).toHaveTextContent("編集中の内容はこの画面に残っています");
  expect(screen.getByText(/保存できていません/)).toHaveAttribute(
    "data-kind",
    "unsaved",
  );
  fireEvent.click(screen.getByRole("button", { name: "再試行" }));
  await waitFor(() => expect(api.saveRevision).toHaveBeenCalledTimes(2));
  const [first, second] = api.saveRevision.mock.calls;
  expect(second[3]).toBe(first[3]);
  expect(second[1]).toBe("edit-1");
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

test("replays an unconfirmed save with its own key before saving newer content", async () => {
  api.saveRevision
    .mockRejectedValueOnce(new HttpError(0, "切断"))
    .mockResolvedValueOnce(saved(2))
    .mockResolvedValueOnce(saved(3));
  await open();
  edit();
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  edit();
  fireEvent.click(saveButton());
  await waitFor(() => expect(api.saveRevision).toHaveBeenCalledTimes(3));
  const [first, replay, newer] = api.saveRevision.mock.calls;
  expect(replay[1]).toBe("edit-1");
  expect(replay[3]).toBe(first[3]);
  expect(newer[1]).toBe("edit-2");
  expect(newer[2]).toBe('"r-r2"');
  expect(newer[3]).not.toBe(first[3]);
  await waitFor(() =>
    expect(screen.getByText(/保存済み · 版 3/)).toBeInTheDocument(),
  );
});

test("a 412 after a lost response is recognized when the current revision is what we sent", async () => {
  const sha = createHash("sha256").update("edit-1").digest("hex");
  api.saveRevision.mockRejectedValue(new HttpError(412, "古い"));
  api.getDiagram
    .mockResolvedValueOnce({ info: info(), etag: '"r-r1"' })
    .mockResolvedValue({
      info: info({
        revision_id: "r2",
        revision_number: 2,
        etag: '"r-r2"',
        sha256: sha,
      }),
      etag: '"r-r2"',
    });
  const onChanged = vi.fn();
  await open(onChanged);
  edit();
  fireEvent.click(saveButton());
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText(/保存済み · 版 2/)).toBeInTheDocument();
});

test("a 412 whose current revision differs stays a conflict", async () => {
  api.saveRevision.mockRejectedValue(new HttpError(412, "古い"));
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", etag: '"r-r2"', sha256: "other" }),
    etag: '"r-r2"',
  });
  await open();
  edit();
  fireEvent.click(saveButton());
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "他の場所で更新された",
  );
});

test("a 412 keeps the input, offers the options and can continue editing", async () => {
  api.saveRevision.mockRejectedValue(new HttpError(412, "古い版です"));
  await open();
  edit();
  fireEvent.click(saveButton());
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("他の場所で更新された");
  expect(
    screen.getByRole("button", { name: /最新版を読み込む/ }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: /\.excalidrawで書き出す/ }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "このまま編集を続ける" }));
  expect(screen.queryByRole("button", { name: /最新版を読み込む/ })).toBeNull();
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(screen.getByText(/保存できていません/)).toBeInTheDocument();
});

test("reloading the latest version replaces the canvas and drops the draft", async () => {
  api.saveRevision.mockRejectedValue(new HttpError(412, "古い"));
  api.createDraft.mockResolvedValue({
    draft: {
      id: "dr",
      version: 1,
      base_revision: "r1",
      etag: '"d-dr-1"',
      updated: 5,
    },
    etag: '"d-dr-1"',
  });
  await open();
  edit();
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  api.getContent.mockResolvedValue({ scene: "scene-3", etag: '"r-r3"' });
  fireEvent.click(screen.getByRole("button", { name: /最新版を読み込む/ }));
  await waitFor(() =>
    expect(screen.getByTestId("canvas")).toHaveAttribute(
      "data-scene",
      "scene-3",
    ),
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText(/保存済み · 版 3/)).toBeInTheDocument();
});

test("a 409 from a replayed key is not treated as a version conflict", async () => {
  api.saveRevision
    .mockRejectedValueOnce(new HttpError(409, "内容が異なります"))
    .mockResolvedValueOnce(saved());
  await open();
  edit();
  fireEvent.click(saveButton());
  expect(await screen.findByRole("alert")).toHaveTextContent("もう一度保存");
  fireEvent.click(screen.getByRole("button", { name: "再試行" }));
  await waitFor(() => expect(api.saveRevision).toHaveBeenCalledTimes(2));
  expect(api.saveRevision.mock.calls[1][3]).not.toBe(
    api.saveRevision.mock.calls[0][3],
  );
});

test("autosaves a draft after idle time, at most every 15 seconds, and only on change", async () => {
  const draft = (version: number) => ({
    draft: {
      id: "dr",
      version,
      base_revision: "r1",
      etag: `"d-dr-${version}"`,
      updated: 5,
    },
    etag: `"d-dr-${version}"`,
  });
  api.createDraft.mockResolvedValue(draft(1));
  api.updateDraft.mockResolvedValue(draft(2));
  realTiming();
  render(<DiagramEditor job={job} />);
  await screen.findByTestId("canvas");
  vi.useFakeTimers();
  // Nothing changed yet: no draft however long we wait.
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(api.createDraft).not.toHaveBeenCalled();

  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_IDLE_MS - 1));
  expect(api.createDraft).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(api.createDraft).toHaveBeenCalledWith(job.id, "edit-1", '"r-r1"');
  expect(screen.getByText(/下書きを自動保存しました/)).toBeInTheDocument();

  // A quick second edit waits for the minimum interval.
  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_IDLE_MS + 100));
  expect(api.updateDraft).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_INTERVAL_MS));
  expect(api.updateDraft).toHaveBeenCalledWith(
    job.id,
    "dr",
    "edit-2",
    '"d-dr-1"',
  );
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(api.updateDraft).toHaveBeenCalledTimes(1);
});

test("resumes an existing draft and commits it with both ETags", async () => {
  api.getDiagram.mockResolvedValue({
    info: info({
      draft: {
        id: "dr",
        version: 3,
        base_revision: "r1",
        etag: '"d-dr-3"',
        updated: 5,
      },
    }),
    etag: '"r-r1"',
  });
  api.getDraftContent.mockResolvedValue({
    scene: "draft-scene",
    etag: '"d-dr-3"',
  });
  api.commitDraft.mockResolvedValue(saved());
  await open();
  expect(api.getContent).not.toHaveBeenCalled();
  expect(screen.getByTestId("canvas")).toHaveAttribute(
    "data-scene",
    "draft-scene",
  );
  expect(screen.getByText(/未保存の変更があります/)).toBeInTheDocument();
  expect(
    screen.getByText(/保存していなかった下書きを開きました/),
  ).toBeInTheDocument();
  edit();
  api.updateDraft.mockResolvedValue({
    draft: {
      id: "dr",
      version: 4,
      base_revision: "r1",
      etag: '"d-dr-4"',
      updated: 6,
    },
    etag: '"d-dr-4"',
  });
  fireEvent.click(saveButton());
  await waitFor(() => expect(api.commitDraft).toHaveBeenCalledTimes(1));
  // The latest content is pushed to the draft before it is committed.
  expect(api.updateDraft).toHaveBeenCalledWith(
    job.id,
    "dr",
    "edit-1",
    '"d-dr-3"',
  );
  expect(api.commitDraft).toHaveBeenCalledWith(
    job.id,
    "dr",
    { source: '"r-r1"', draft: '"d-dr-4"' },
    expect.any(String),
  );
  expect(api.saveRevision).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(screen.getByText(/保存済み · 版 2/)).toBeInTheDocument(),
  );
});

test("a draft based on an older revision is opened with a conflict notice", async () => {
  api.getDiagram.mockResolvedValue({
    info: info({
      draft: {
        id: "dr",
        version: 1,
        base_revision: "r0",
        etag: '"d-dr-1"',
        updated: 5,
      },
    }),
    etag: '"r-r1"',
  });
  api.getDraftContent.mockResolvedValue({
    scene: "old-draft",
    etag: '"d-dr-1"',
  });
  await open();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "古い版を基にしている",
  );
});

test("lists versions and restores one after confirmation", async () => {
  const onChanged = vi.fn();
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", revision_number: 2, etag: '"r-r2"' }),
    etag: '"r-r2"',
  });
  api.restoreRevision.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  api.getContent
    .mockResolvedValueOnce({ scene: "scene-2", etag: "" })
    .mockResolvedValueOnce({ scene: "restored-1", etag: "" });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  await open(onChanged);
  fireEvent.click(screen.getByRole("button", { name: "版の履歴" }));
  expect(await screen.findByText(/版 2（現在）/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "版 2 に戻す" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "版 1 に戻す" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(confirm).toHaveBeenCalled();
  expect(api.restoreRevision).toHaveBeenCalledWith(
    job.id,
    "r1",
    '"r-r2"',
    expect.any(String),
  );
  await waitFor(() =>
    expect(screen.getByTestId("canvas")).toHaveAttribute(
      "data-scene",
      "restored-1",
    ),
  );
  expect(screen.getByText(/保存済み · 版 3/)).toBeInTheDocument();
});

test("declining the restore confirmation changes nothing", async () => {
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", revision_number: 2, etag: '"r-r2"' }),
    etag: '"r-r2"',
  });
  vi.spyOn(window, "confirm").mockReturnValue(false);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "版の履歴" }));
  fireEvent.click(await screen.findByRole("button", { name: "版 1 に戻す" }));
  expect(api.restoreRevision).not.toHaveBeenCalled();
});

test("warns before leaving only while there are unsaved changes", async () => {
  await open();
  const clean = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
  edit();
  const dirty = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
});

test("exports the current scene and says when WebP fell back to JPEG", async () => {
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:x"),
    revokeObjectURL: vi.fn(),
  });
  exporter.exportSvg.mockResolvedValue(new Blob(["s"]));
  exporter.exportRaster.mockResolvedValue({
    blob: new Blob(["j"]),
    format: "jpeg",
  });
  await open();
  edit();
  fireEvent.click(screen.getByRole("button", { name: "SVGで書き出す" }));
  await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  expect(exporter.exportSvg).toHaveBeenCalledWith("edit-1");
  fireEvent.click(screen.getByRole("button", { name: "WebPで書き出す" }));
  expect(await screen.findByText(/JPEGで書き出しました/)).toBeInTheDocument();
  expect(exporter.exportRaster).toHaveBeenCalledWith("edit-1", "webp");
  expect(click).toHaveBeenCalledTimes(2);
});

test("an export error does not touch the editing state", async () => {
  exporter.exportSvg.mockRejectedValue(new Error("書き出す図形がありません。"));
  await open();
  edit();
  fireEvent.click(screen.getByRole("button", { name: "SVGで書き出す" }));
  expect(
    await screen.findByText(/書き出す図形がありません/),
  ).toBeInTheDocument();
  expect(screen.getByText(/未保存の変更があります/)).toBeInTheDocument();
});

const dr = (version: number, base = "r1") => ({
  draft: {
    id: "dr",
    version,
    base_revision: base,
    etag: `"d-dr-${version}"`,
    updated: 5,
  },
  etag: `"d-dr-${version}"`,
});
function resumeDraft() {
  api.getDiagram.mockResolvedValue({
    info: info({ draft: dr(3).draft }),
    etag: '"r-r1"',
  });
  api.getDraftContent.mockResolvedValueOnce({
    scene: "draft-scene",
    etag: '"d-dr-3"',
  });
}

test("a draft update 412 is recovered when the stored draft is what we sent", async () => {
  resumeDraft();
  api.updateDraft.mockRejectedValueOnce(new HttpError(412, "古い"));
  api.getDraftContent.mockResolvedValueOnce({
    scene: "edit-1",
    etag: '"d-dr-4"',
  });
  api.getDraft.mockResolvedValue(dr(4));
  api.commitDraft.mockResolvedValue(saved());
  await open();
  edit();
  fireEvent.click(saveButton());
  await waitFor(() => expect(api.commitDraft).toHaveBeenCalledTimes(1));
  expect(api.commitDraft.mock.calls[0][2]).toEqual({
    source: '"r-r1"',
    draft: '"d-dr-4"',
  });
});

test("a draft update 412 is recovered when an earlier unconfirmed upload advanced the draft", async () => {
  resumeDraft();
  api.updateDraft
    .mockRejectedValueOnce(new HttpError(0, "切断"))
    .mockRejectedValueOnce(new HttpError(412, "古い"))
    .mockResolvedValueOnce(dr(5));
  api.getDraftContent.mockResolvedValueOnce({
    scene: "edit-1",
    etag: '"d-dr-4"',
  });
  api.commitDraft.mockResolvedValue(saved());
  await open();
  edit();
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  edit();
  fireEvent.click(saveButton());
  await waitFor(() => expect(api.commitDraft).toHaveBeenCalledTimes(1));
  expect(api.updateDraft).toHaveBeenLastCalledWith(
    job.id,
    "dr",
    "edit-2",
    '"d-dr-4"',
  );
  expect(api.commitDraft.mock.calls[0][2].draft).toBe('"d-dr-5"');
});

test("a draft that is really newer on the server is a conflict", async () => {
  resumeDraft();
  api.updateDraft.mockRejectedValue(new HttpError(412, "古い"));
  api.getDraftContent.mockResolvedValueOnce({
    scene: "someone-else",
    etag: '"d-dr-9"',
  });
  await open();
  edit();
  fireEvent.click(saveButton());
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "他の場所で更新された",
  );
  expect(api.commitDraft).not.toHaveBeenCalled();
});

test("a canvas that failed to render reopens on the current scene without uploading older content", async () => {
  const draft = (n: number) => ({
    draft: { id: "dr", base_revision: "r1", updated: n } as never,
    etag: `"d-dr-${n}"`,
  });
  api.createDraft.mockResolvedValue(draft(1));
  api.updateDraft.mockResolvedValue(draft(2));
  const noise = vi.spyOn(console, "error").mockImplementation(() => {});
  realTiming();
  render(<DiagramEditor job={job} />);
  await screen.findByTestId("canvas");
  vi.useFakeTimers();
  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_IDLE_MS));
  expect(api.createDraft).toHaveBeenCalledWith(job.id, "edit-1", '"r-r1"');
  edit();

  fireEvent.click(screen.getByRole("button", { name: "crash" }));
  expect(screen.queryByTestId("canvas")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "直前までの編集内容から開き直します",
  );
  fireEvent.click(screen.getByRole("button", { name: "もう一度読み込む" }));
  await act(() => vi.advanceTimersByTimeAsync(0));
  // Reopened on the edit, not on the revision that was first loaded.
  expect(screen.getByTestId("canvas")).toHaveAttribute("data-scene", "edit-2");
  expect(screen.getByText(/未保存の変更があります/)).toBeInTheDocument();

  // Only the scene being edited is ever uploaded.
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_INTERVAL_MS * 2));
  expect(api.createDraft).toHaveBeenCalledTimes(1);
  expect(api.updateDraft).toHaveBeenCalledTimes(1);
  expect(api.updateDraft).toHaveBeenCalledWith(
    job.id,
    "dr",
    "edit-2",
    '"d-dr-1"',
  );

  // Edits after the retry are treated as edits again.
  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_INTERVAL_MS));
  expect(api.updateDraft).toHaveBeenLastCalledWith(
    job.id,
    "dr",
    "edit-3",
    '"d-dr-2"',
  );
  noise.mockRestore();
});

async function autosaveFirstDraft() {
  realTiming();
  render(<DiagramEditor job={job} />);
  await screen.findByTestId("canvas");
  vi.useFakeTimers();
  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_IDLE_MS));
}

test("an unknown draft on the server is adopted when it has the same base", async () => {
  api.createDraft.mockRejectedValue(
    new HttpError(409, "下書きがあります", {
      detail: { draft_id: "old" },
    }),
  );
  api.getDraft.mockResolvedValue({
    ...dr(2),
    draft: { ...dr(2).draft, id: "old" },
  });
  api.updateDraft.mockResolvedValue(dr(3));
  await autosaveFirstDraft();
  expect(api.getDraft).toHaveBeenCalledWith(job.id, "old");
  expect(api.updateDraft).toHaveBeenCalledWith(
    job.id,
    "old",
    "edit-1",
    '"d-dr-2"',
  );
  expect(api.discardDraft).not.toHaveBeenCalled();
  expect(screen.getByText(/下書きを自動保存しました/)).toBeInTheDocument();
});

test("an unknown draft based on another revision is discarded and replaced", async () => {
  api.createDraft
    .mockRejectedValueOnce(
      new HttpError(409, `下書き ${"c".repeat(32)} があります`),
    )
    .mockResolvedValueOnce(dr(1));
  api.getDraft.mockResolvedValue({
    ...dr(2, "r0"),
    draft: { ...dr(2, "r0").draft, id: "c".repeat(32) },
  });
  await autosaveFirstDraft();
  expect(api.discardDraft).toHaveBeenCalledWith(
    job.id,
    "c".repeat(32),
    '"d-dr-2"',
  );
  expect(api.createDraft).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/下書きを自動保存しました/)).toBeInTheDocument();
});

test("a draft left on the server by a plain save is discarded", async () => {
  api.saveRevision.mockResolvedValue({
    info: info({
      revision_id: "r2",
      revision_number: 2,
      etag: '"r-r2"',
      draft: dr(1, "r1").draft,
    }),
    etag: '"r-r2"',
  });
  await open();
  edit();
  fireEvent.click(saveButton());
  await waitFor(() =>
    expect(api.discardDraft).toHaveBeenCalledWith(job.id, "dr", '"d-dr-1"'),
  );
});

test("restore discards the stale draft only after the restore succeeded", async () => {
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", revision_number: 2, etag: '"r-r2"' }),
    etag: '"r-r2"',
  });
  api.restoreRevision.mockResolvedValue({
    info: info({
      revision_id: "r3",
      revision_number: 3,
      etag: '"r-r3"',
      draft: dr(1, "r2").draft,
    }),
    etag: '"r-r3"',
  });
  api.getContent
    .mockResolvedValueOnce({ scene: "scene-2", etag: "" })
    .mockResolvedValueOnce({ scene: "restored", etag: "" });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "版の履歴" }));
  fireEvent.click(await screen.findByRole("button", { name: "版 1 に戻す" }));
  await waitFor(() => expect(api.discardDraft).toHaveBeenCalledTimes(1));
  expect(api.discardDraft).toHaveBeenCalledWith(job.id, "dr", '"d-dr-1"');
  expect(api.restoreRevision.mock.invocationCallOrder[0]).toBeLessThan(
    api.discardDraft.mock.invocationCallOrder[0],
  );
});

test("a failed restore leaves the draft alone", async () => {
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", revision_number: 2, etag: '"r-r2"' }),
    etag: '"r-r2"',
  });
  api.restoreRevision.mockRejectedValue(new HttpError(412, "古い"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "版の履歴" }));
  fireEvent.click(await screen.findByRole("button", { name: "版 1 に戻す" }));
  await screen.findByRole("alert");
  expect(api.discardDraft).not.toHaveBeenCalled();
});

test("keeps the restored revision when its content cannot be loaded", async () => {
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", revision_number: 2, etag: '"r-r2"' }),
    etag: '"r-r2"',
  });
  api.restoreRevision.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  api.getContent
    .mockResolvedValueOnce({ scene: "scene-2", etag: "" })
    .mockRejectedValueOnce(new HttpError(0, "切断"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const onChanged = vi.fn();
  await open(onChanged);
  fireEvent.click(screen.getByRole("button", { name: "版の履歴" }));
  fireEvent.click(await screen.findByRole("button", { name: "版 1 に戻す" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "復元されましたが",
  );
  expect(onChanged).toHaveBeenCalled();
  expect(screen.getByText(/版 3/)).toBeInTheDocument();
  expect(saveButton()).toBeDisabled();
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  api.getContent.mockResolvedValueOnce({ scene: "scene-3", etag: "" });
  fireEvent.click(screen.getByRole("button", { name: "内容を再読み込み" }));
  await waitFor(() =>
    expect(screen.getByTestId("canvas")).toHaveAttribute(
      "data-scene",
      "scene-3",
    ),
  );
});

test("reloading waits for an upload that is in flight before discarding the draft", async () => {
  resumeDraft();
  let finish!: (value: unknown) => void;
  api.updateDraft.mockReturnValue(new Promise((resolve) => (finish = resolve)));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await open();
  edit();
  Object.defineProperty(document, "visibilityState", {
    value: "hidden",
    configurable: true,
  });
  document.dispatchEvent(new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", {
    value: "visible",
    configurable: true,
  });
  await waitFor(() => expect(api.updateDraft).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "下書きを破棄" }));
  await act(async () => {
    await Promise.resolve();
  });
  expect(api.discardDraft).not.toHaveBeenCalled();
  await act(async () => finish(dr(4)));
  await waitFor(() =>
    expect(api.discardDraft).toHaveBeenCalledWith(job.id, "dr", '"d-dr-4"'),
  );
});

test("input that could not be saved survives leaving the editor", async () => {
  api.saveRevision.mockRejectedValue(new HttpError(412, "古い"));
  const first = await open();
  edit();
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  first.unmount();
  expect(
    window.sessionStorage.getItem(`docling-diagram-unsaved:${job.id}`),
  ).toContain("edit-1");
  await open();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "保存されないまま閉じた",
  );
  fireEvent.click(screen.getByRole("button", { name: "その内容を開く" }));
  await waitFor(() =>
    expect(screen.getByTestId("canvas")).toHaveAttribute(
      "data-scene",
      "edit-1",
    ),
  );
  expect(screen.getByText(/未保存の変更があります/)).toBeInTheDocument();
  expect(
    window.sessionStorage.getItem(`docling-diagram-unsaved:${job.id}`),
  ).toBeNull();
});

test("the stashed input can be discarded", async () => {
  window.sessionStorage.setItem(
    `docling-diagram-unsaved:${job.id}`,
    JSON.stringify({ scene: "lost", base: "r1", saved: 1 }),
  );
  await open();
  fireEvent.click(await screen.findByRole("button", { name: "破棄する" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(
    window.sessionStorage.getItem(`docling-diagram-unsaved:${job.id}`),
  ).toBeNull();
});

test("a 422 shows the server detail", async () => {
  api.saveRevision.mockRejectedValue(
    new HttpError(422, "要素 abc の種類 embeddable は使用できません。"),
  );
  await open();
  edit();
  fireEvent.click(saveButton());
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("受け付けませんでした");
  expect(alert).toHaveTextContent("要素 abc の種類 embeddable");
});

test("a permanently refused draft is not retried until the scene changes", async () => {
  api.createDraft.mockRejectedValue(
    new HttpError(422, "要素 abc の種類 embeddable は使用できません。"),
  );
  await autosaveFirstDraft();
  expect(api.createDraft).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("alert")).toHaveTextContent("受け付けませんでした");
  await act(() => vi.advanceTimersByTimeAsync(10 * DRAFT_INTERVAL_MS));
  expect(api.createDraft).toHaveBeenCalledTimes(1);
  // An edit is new content, so it is tried again.
  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_INTERVAL_MS));
  expect(api.createDraft).toHaveBeenCalledTimes(2);
  expect(api.createDraft.mock.calls[1][1]).toBe("edit-2");
});

test("an upload that finishes after unmount neither reschedules nor shows anything", async () => {
  let finish!: (value: unknown) => void;
  api.createDraft.mockReturnValue(new Promise((resolve) => (finish = resolve)));
  realTiming();
  const view = render(<DiagramEditor job={job} />);
  await screen.findByTestId("canvas");
  vi.useFakeTimers();
  edit();
  await act(() => vi.advanceTimersByTimeAsync(DRAFT_IDLE_MS));
  expect(api.createDraft).toHaveBeenCalledTimes(1);
  edit();
  expect(vi.getTimerCount()).toBe(1);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
  await act(async () => finish(dr(1)));
  expect(vi.getTimerCount()).toBe(0);
  expect(api.updateDraft).not.toHaveBeenCalled();
});

test("an obsolete pending commit is dropped when the server did not apply it", async () => {
  resumeDraft();
  api.updateDraft.mockResolvedValueOnce(dr(4)).mockResolvedValueOnce(dr(5));
  api.commitDraft
    .mockRejectedValueOnce(new HttpError(0, "切断"))
    .mockRejectedValueOnce(new HttpError(412, "古い"))
    .mockResolvedValueOnce(saved());
  await open();
  edit();
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  edit();
  fireEvent.click(saveButton());
  await waitFor(() => expect(api.commitDraft).toHaveBeenCalledTimes(3));
  const [first, replay, fresh] = api.commitDraft.mock.calls;
  expect(replay[3]).toBe(first[3]);
  expect(fresh[3]).not.toBe(first[3]);
  expect(fresh[2]).toEqual({ source: '"r-r1"', draft: '"d-dr-5"' });
  await waitFor(() =>
    expect(screen.getByText(/保存済み · 版 2/)).toBeInTheDocument(),
  );
});

async function failRestore(error: HttpError) {
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r2", revision_number: 2, etag: '"r-r2"' }),
    etag: '"r-r2"',
  });
  api.restoreRevision.mockRejectedValueOnce(error);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "版の履歴" }));
  fireEvent.click(await screen.findByRole("button", { name: "版 1 に戻す" }));
  await screen.findByRole("alert");
}

test("retrying a failed restore repeats the restore with the same key", async () => {
  api.restoreRevision.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  await failRestore(new HttpError(0, "切断"));
  api.getContent.mockResolvedValueOnce({ scene: "restored", etag: "" });
  fireEvent.click(screen.getByRole("button", { name: "再試行" }));
  await waitFor(() => expect(api.restoreRevision).toHaveBeenCalledTimes(2));
  expect(api.saveRevision).not.toHaveBeenCalled();
  const [first, again] = api.restoreRevision.mock.calls;
  expect(again.slice(1, 4)).toEqual(first.slice(1, 4));
  await waitFor(() =>
    expect(screen.getByTestId("canvas")).toHaveAttribute(
      "data-scene",
      "restored",
    ),
  );
});

test("a restore key is not reused once the base ETag changed or the key was refused", async () => {
  api.restoreRevision.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  await failRestore(new HttpError(409, "内容が異なります"));
  expect(screen.getByRole("alert")).toHaveTextContent("復元の再送内容");
  fireEvent.click(screen.getByRole("button", { name: "再試行" }));
  await waitFor(() => expect(api.restoreRevision).toHaveBeenCalledTimes(2));
  expect(api.restoreRevision.mock.calls[1][3]).not.toBe(
    api.restoreRevision.mock.calls[0][3],
  );
});

test("a draft that moved on is re-read before it is discarded", async () => {
  resumeDraft();
  api.discardDraft
    .mockRejectedValueOnce(new HttpError(412, "古い"))
    .mockResolvedValue(undefined);
  api.getDraft.mockResolvedValue(dr(6));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "下書きを破棄" }));
  await waitFor(() => expect(api.discardDraft).toHaveBeenCalledTimes(2));
  expect(api.discardDraft.mock.calls[1]).toEqual([job.id, "dr", '"d-dr-6"']);
  await waitFor(() => expect(api.getDiagram).toHaveBeenCalledTimes(2));
});

test("when the draft cannot be discarded the latest version is not loaded", async () => {
  resumeDraft();
  api.discardDraft.mockRejectedValue(new HttpError(412, "古い"));
  api.getDraft.mockResolvedValue(dr(6));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await open();
  fireEvent.click(screen.getByRole("button", { name: "下書きを破棄" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "下書きを破棄できませんでした",
  );
  expect(api.getDiagram).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId("canvas")).toHaveAttribute(
    "data-scene",
    "draft-scene",
  );
});

test("a failed reload is shown and the editor keeps what is open", async () => {
  api.saveRevision.mockRejectedValue(new HttpError(412, "古い"));
  await open();
  edit();
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  api.getDiagram.mockRejectedValueOnce(new HttpError(500, "障害"));
  fireEvent.click(screen.getByRole("button", { name: /最新版を読み込む/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "最新の内容を読み込めませんでした",
  );
  expect(screen.getByTestId("canvas")).toHaveAttribute("data-scene", "scene-1");
  api.getDiagram.mockResolvedValue({
    info: info({ revision_id: "r3", revision_number: 3, etag: '"r-r3"' }),
    etag: '"r-r3"',
  });
  api.getContent.mockResolvedValueOnce({ scene: "scene-3", etag: "" });
  fireEvent.click(screen.getByRole("button", { name: "内容を再読み込み" }));
  await waitFor(() =>
    expect(screen.getByTestId("canvas")).toHaveAttribute(
      "data-scene",
      "scene-3",
    ),
  );
});
