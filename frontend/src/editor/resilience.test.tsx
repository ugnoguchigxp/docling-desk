import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { Editor } from "./main";

vi.mock("./RawEditor", () => ({
  RawEditor: () => <div>rich editor</div>,
  sourceEdit: (_original: string, next: string) => next,
}));

const draft = {
  draft_id: "draft",
  source_id: "source",
  base_revision: null,
  base_etag: null,
  body: "original",
  metadata: {
    title: "Title",
    collection_id: "docs",
    source_kind: "wiki",
    language: "ja",
  },
  etag: '"draft:1"',
  sha256: "hash",
  version: 1,
  state: "draft",
  attachments: [],
};
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const settle = (ms = 0) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const hidden = (value: boolean) =>
  Object.defineProperty(document, "hidden", { value, configurable: true });

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  hidden(false);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type Handler = (name: string, args: Record<string, unknown>) => Response;
function serve(options: { session?: () => Response; tool: Handler }) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/editor/challenges")
      return json({ challenge: "challenge", code: "CODE" });
    if (url === "/editor/challenges/challenge") return json({ session: "s" });
    if (url === "/editor/session/s")
      return (
        options.session?.() ??
        json({ draft_id: "draft", scope: { collection_id: "docs" } })
      );
    const call = JSON.parse(init!.body as string);
    return options.tool(call.name, call.arguments);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const sessionCalls = (fetchMock: ReturnType<typeof serve>) =>
  fetchMock.mock.calls.filter((call) => call[0] === "/editor/session/s").length;

it("keeps unsaved text visible and recoverable when the session is lost, and warns before leaving", async () => {
  let polls = 0;
  const fetchMock = serve({
    session: () =>
      ++polls > 1
        ? json({ detail: "expired" }, 401)
        : json({ draft_id: "draft" }),
    tool: (name) =>
      name === "get_document_draft"
        ? json(draft)
        : json({ detail: "boom" }, 500),
  });
  render(<Editor />);
  await settle(10);
  screen.getByText("rich editor");
  fireEvent.click(screen.getByRole("button", { name: "ソース編集" }));
  const source = screen.getByRole("textbox", { name: "Markdownソース" });
  fireEvent.change(source, { target: { value: "precious unsaved text" } });
  await settle(1000); // autosave attempt fails, text stays dirty
  await settle(15000); // session poll answers 401
  expect(sessionCalls(fetchMock)).toBeGreaterThan(1);
  expect(screen.getByText(/編集内容は保存されていません/)).toBeInTheDocument();
  expect(source).toHaveValue("precious unsaved text");
  expect(source).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "退避した入力" })).toHaveValue(
    "precious unsaved text",
  );
  expect(
    screen.getByRole("button", { name: "Markdownとして保存" }),
  ).toBeEnabled();
  expect(screen.getByRole("button", { name: "コピー" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "下書きを保存" })).toBeDisabled();
  const leave = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(leave);
  expect(leave.defaultPrevented).toBe(true);
});

it("does not block leaving when nothing is unsaved", async () => {
  serve({ tool: () => json(draft) });
  render(<Editor />);
  await settle(10);
  screen.getByText("rich editor");
  const leave = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(leave);
  expect(leave.defaultPrevented).toBe(false);
});

it("polls the saved document one request at a time, pauses while hidden and stops once indexed", async () => {
  let reads = 0;
  let release: (() => void) | null = null;
  const fetchMock = serve({
    tool: (name) => {
      if (name === "get_document_draft") return json(draft);
      if (name === "commit_document_draft")
        return json({ source_id: "source", source_revision: "r1" });
      throw new Error("unexpected " + name);
    },
  });
  const base = fetchMock.getMockImplementation()!;
  const indexed = (ready: boolean) => ({
    source_id: "source",
    source_revision: "r1",
    etag: "e",
    content_kind: "original_markdown",
    metadata: draft.metadata,
    status: {
      search: ready ? "ready" : "pending",
      fts_ready: ready,
      semantic_ready: ready,
      semantic_configured: true,
      error_code: null,
      evidence_revision: null,
    },
  });
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (typeof init?.body === "string") {
      const call = JSON.parse(init.body);
      if (call.name === "get_document") {
        reads++;
        // The first poll is slower than the interval.
        if (reads === 2)
          await new Promise<void>((resolve) => (release = resolve));
        return json(indexed(reads >= 3));
      }
    }
    return base(url, init);
  });
  render(<Editor />);
  await settle(10);
  screen.getByText("rich editor");
  fireEvent.click(screen.getByRole("button", { name: "保存版を確定" }));
  await settle(10);
  expect(reads).toBe(1); // the one read made by commit itself
  hidden(true);
  await settle(6000);
  expect(reads).toBe(1); // hidden tab: no request
  hidden(false);
  await settle(2000);
  expect(reads).toBe(2);
  await settle(10000); // still in flight: nothing piles up
  expect(reads).toBe(2);
  release!();
  await settle(0);
  await settle(2000);
  expect(reads).toBe(3);
  expect(screen.getByRole("status")).toHaveTextContent("意味検索反映完了");
  await settle(20000); // indexed: polling is over
  expect(reads).toBe(3);
});

it("shows a readable message for a non-JSON gateway error", async () => {
  serve({
    session: () =>
      new Response("<html><body>Bad Gateway</body></html>", { status: 502 }),
    tool: () => json(draft),
  });
  render(<Editor />);
  await settle(10);
  const alert = screen.getByText(/HTTP 502/);
  expect(alert.textContent).not.toMatch(/SyntaxError|Unexpected token/);
});
