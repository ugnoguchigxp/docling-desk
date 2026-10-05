import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../lib/types";
import type { ViewerManifest } from "../viewer/reference";

const roots = vi.hoisted(() => ({
  list: [] as Array<{ unmount: () => void }>,
}));

vi.mock("react-dom/client", async () => {
  const actual = await vi.importActual<typeof import("react-dom/client")>(
    "react-dom/client",
  );
  return {
    ...actual,
    createRoot(
      container: Element,
      options?: Parameters<typeof actual.createRoot>[1],
    ) {
      const root = actual.createRoot(container, options);
      roots.list.push(root);
      return root;
    },
  };
});

const posted: unknown[] = [];
const realSetTimeout = globalThis.setTimeout;
let fetchHandler: (url: string, init?: RequestInit) => Promise<Response> | Response =
  () => new Response("missing", { status: 404 });

function job(): Job {
  return {
    id: "job",
    filename: "embedded.pdf",
    original_filename: null,
    folder_id: null,
    state: "success",
    created: 1,
    duration: 1,
    pages: 4,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: false,
  };
}

function manifest(
  kind: ViewerManifest["kind"] = "page",
  location: ViewerManifest["reference"]["location"] = {
    kind: "page",
    number: 2,
  },
): ViewerManifest {
  return {
    reference: {
      version: 1,
      source_id: "source",
      source_revision: "srev",
      evidence_revision: "erev",
      location,
    },
    job: job(),
    kind,
    units: 4,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function refUrl(value: unknown) {
  return `/?ref=${encodeURIComponent(JSON.stringify(value))}`;
}

async function boot(search: string) {
  window.history.replaceState(null, "", search);
  document.body.innerHTML = '<div id="root"></div>';
  await act(async () => {
    await import("./main");
  });
}

async function tick() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function heights() {
  return posted.flatMap((message) => {
    if (
      message &&
      typeof message === "object" &&
      "type" in message &&
      message.type === "iframe:height" &&
      "height" in message &&
      typeof message.height === "number"
    )
      return [message.height];
    return [];
  });
}

beforeEach(() => {
  posted.length = 0;
  document.body.innerHTML = '<div id="root"></div>';
  vi.resetModules();
  vi.spyOn(globalThis, "setTimeout").mockImplementation(((
    fn: TimerHandler,
    ms?: number,
    ...args: unknown[]
  ) => {
    const delay =
      (ms === 1000 || ms === 2000) && String(fn).includes("poll") ? 0 : ms;
    return realSetTimeout(fn as () => void, delay, ...(args as []));
  }) as unknown as typeof setTimeout);
  vi.spyOn(window, "postMessage").mockImplementation((data) => {
    posted.push(data);
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: 0,
  });
  fetchHandler = () => new Response("missing", { status: 404 });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(fetchHandler(String(input), init)),
    ),
  );
});

afterEach(() => {
  for (const root of roots.list) root.unmount();
  roots.list.length = 0;
  cleanup();
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("embedded viewer", () => {
  it("connects, reports height, asks, and reconnects", async () => {
    const waits: Array<(value: Response) => void> = [];
    const hold = () =>
      new Promise<Response>((resolve) => {
        waits.push(resolve);
      });
    let polls = 0;
    fetchHandler = (url, init) => {
      if (url.endsWith("/viewer/challenges") && init?.method === "POST")
        return json({ code: "CODE99", challenge: "ch-1" });
      if (url.includes("/viewer/challenges/ch-1")) {
        polls += 1;
        return hold();
      }
      if (url.includes("/viewer/session/sess/manifest")) return json(manifest());
      return new Response("missing", { status: 404 });
    };
    await boot(
      refUrl({
        source_id: "source",
        source_revision: "srev",
        evidence_revision: "erev",
        location: { kind: "page", number: 2 },
      }),
    );
    expect(await screen.findByLabelText("接続コード")).toHaveTextContent(
      "CODE99",
    );
    expect(screen.getByRole("status")).toHaveTextContent("接続コードは3分間有効です");
    expect(heights().at(-1)).toBe(760);
    fireEvent.click(
      screen.getByRole("button", { name: "接続コードをチャット入力へ" }),
    );
    expect(posted).toContainEqual(
      expect.objectContaining({
        type: "input:prompt",
        text: expect.stringContaining("CODE99"),
      }),
    );
    await tick();
    expect(waits.length).toBeGreaterThan(0);
    await act(async () => {
      waits.shift()?.(json({ status: "pending" }));
    });
    await tick();
    await act(async () => {
      waits.shift()?.(json({ status: "ready", session: "sess" }));
    });
    expect(polls).toBeGreaterThan(1);
    expect(await screen.findByText("embedded.pdf")).toBeInTheDocument();
    expect(screen.getByText("位置 1")).toBeInTheDocument();
    const frame = document.getElementById("original");
    if (!(frame instanceof HTMLIFrameElement) || !frame.contentWindow)
      throw new Error("missing frame");
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frame.contentWindow,
          data: { type: "docling-unit-current", jobId: "job", number: 3 },
        }),
      );
    });
    expect(screen.getByText("位置 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "この箇所について質問" }));
    expect(screen.getByLabelText("チャットに渡す質問").textContent).toContain(
      "この資料の箇所について説明してください。",
    );
    expect(posted).toContainEqual(
      expect.objectContaining({ type: "input:prompt" }),
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 800,
    });
    fireEvent(window, new Event("resize"));
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 1500,
    });
    fireEvent(window, new Event("resize"));
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });
    fireEvent(window, new Event("resize"));
    expect(heights()).toEqual(expect.arrayContaining([760, 800, 1000, 600]));
    fireEvent.click(screen.getByRole("button", { name: "資料へ再接続" }));
    expect(await screen.findByLabelText("接続コード")).toHaveTextContent(
      "CODE99",
    );
    expect(screen.queryByText("embedded.pdf")).not.toBeInTheDocument();
  });

  it("shows a whole document without a located citation", async () => {
    fetchHandler = (url, init) => {
      if (url.endsWith("/viewer/challenges") && init?.method === "POST")
        return json({ code: "DOC", challenge: "ch-doc" });
      if (url.includes("/viewer/challenges/ch-doc"))
        return json({ status: "ready", session: "doc-session" });
      if (url.includes("/manifest"))
        return json(manifest("document", null));
      return new Response("missing", { status: 404 });
    };
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    expect(await screen.findByText("embedded.pdf")).toBeInTheDocument();
    expect(screen.getByText("文書全体")).toBeInTheDocument();
    expect(screen.getByText("出典位置は未特定です。文書の先頭から表示しています。")).toBeInTheDocument();
  });

  it("maps viewer failures and recovers through reconnect", async () => {
    const cases: [string, string][] = [
      [
        "viewer_session_expired",
        "閲覧の有効期限が切れました。資料へ再接続してください。",
      ],
      [
        "viewer_code_expired",
        "接続コードの有効期限が切れました。新しいコードで再接続してください。",
      ],
      [
        "viewer_session_revoked",
        "閲覧権限が変更されました。資料へ再接続してください。",
      ],
      [
        "viewer_user_forbidden",
        "この利用者には資料の閲覧権限がありません。",
      ],
      [
        "source_not_found",
        "資料が削除されたか、閲覧権限がありません。",
      ],
      [
        "source_changed",
        "資料が更新されました。チャットで検索し直し、新しい資料参照を開いてください。",
      ],
      [
        "evidence_changed",
        "資料の解析結果が更新されました。新しい検索結果から開いてください。",
      ],
      [
        "preview_unavailable",
        "この資料のプレビューはまだ利用できません。",
      ],
      [
        "invalid_location",
        "指定された出典位置を表示できません。資料参照を確認してください。",
      ],
      [
        "viewer_api_unavailable",
        "資料サービスに接続できません。しばらくしてから再接続してください。",
      ],
      ["custom_detail", "custom_detail"],
    ];
    let index = 0;
    fetchHandler = () => json({ detail: cases[index][0] }, 400);
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    for (const [, message] of cases) {
      expect(await screen.findByRole("status")).toHaveTextContent(message);
      if (index < cases.length - 1) {
        index += 1;
        fireEvent.click(screen.getByRole("button", { name: "資料へ再接続" }));
      }
    }
  });

  it("reports a missing, oversized, or unreadable reference", async () => {
    await boot("/embed.html");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "資料参照がありません。",
    );
    for (const root of roots.list) root.unmount();
    roots.list.length = 0;
    document.body.innerHTML = "";
    vi.resetModules();
    await boot(`/?ref=${"a".repeat(2049)}`);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "資料参照がありません。",
    );
    for (const root of roots.list) root.unmount();
    roots.list.length = 0;
    document.body.innerHTML = "";
    vi.resetModules();
    await boot(`/?ref=${encodeURIComponent("{")}`);
    expect(
      await screen.findByRole("button", { name: "資料へ再接続" }),
    ).toBeInTheDocument();
  });

  it("surfaces polling and manifest errors", async () => {
    fetchHandler = (url, init) => {
      if (url.endsWith("/viewer/challenges") && init?.method === "POST")
        return json({ code: "POLL", challenge: "ch-poll" });
      if (url.includes("/viewer/challenges/ch-poll"))
        return json({ detail: "source_not_found" }, 404);
      return new Response("missing", { status: 404 });
    };
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    expect(
      await screen.findByText("資料が削除されたか、閲覧権限がありません。"),
    ).toBeInTheDocument();
    for (const root of roots.list) root.unmount();
    roots.list.length = 0;
    document.body.innerHTML = "";
    vi.resetModules();
    fetchHandler = (url, init) => {
      if (url.endsWith("/viewer/challenges") && init?.method === "POST")
        return json({ code: "MAN", challenge: "ch-man" });
      if (url.includes("/viewer/challenges/ch-man"))
        return json({ status: "ready", session: "man-session" });
      if (url.includes("/manifest"))
        return json({ detail: "viewer_session_revoked" }, 403);
      return new Response("missing", { status: 404 });
    };
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    expect(
      await screen.findByText(
        "閲覧権限が変更されました。資料へ再接続してください。",
      ),
    ).toBeInTheDocument();
  });

  it("ignores responses that arrive after the viewer is closed", async () => {
    let release: ((value: Response) => void) | undefined;
    fetchHandler = () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      });
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    for (const root of roots.list) root.unmount();
    await act(async () => {
      release?.(json({ code: "LATE", challenge: "late" }));
    });
    expect(screen.queryByLabelText("接続コード")).not.toBeInTheDocument();

    roots.list.length = 0;
    document.body.innerHTML = '<div id="root"></div>';
    vi.resetModules();
    let rejectFetch: ((error: unknown) => void) | undefined;
    fetchHandler = () =>
      new Promise<Response>((_resolve, reject) => {
        rejectFetch = reject;
      });
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    for (const root of roots.list) root.unmount();
    await act(async () => {
      rejectFetch?.(new Error("gone"));
    });

    roots.list.length = 0;
    document.body.innerHTML = '<div id="root"></div>';
    vi.resetModules();
    const pollWaits: Array<(value: Response) => void> = [];
    fetchHandler = (url, init) => {
      if (url.endsWith("/viewer/challenges") && init?.method === "POST")
        return json({ code: "WAIT", challenge: "ch-wait" });
      if (url.includes("/viewer/challenges/ch-wait"))
        return new Promise<Response>((resolve) => pollWaits.push(resolve));
      if (url.includes("/manifest"))
        return new Promise<Response>((resolve) => pollWaits.push(resolve));
      return new Response("missing", { status: 404 });
    };
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    await tick();
    for (const root of roots.list) root.unmount();
    await act(async () => {
      pollWaits.shift()?.(json({ status: "pending" }));
    });

    roots.list.length = 0;
    document.body.innerHTML = '<div id="root"></div>';
    vi.resetModules();
    const manifestWaits: Array<(value: Response) => void> = [];
    fetchHandler = (url, init) => {
      if (url.endsWith("/viewer/challenges") && init?.method === "POST")
        return json({ code: "MAN2", challenge: "ch-man2" });
      if (url.includes("/viewer/challenges/ch-man2"))
        return json({ status: "ready", session: "sess-2" });
      if (url.includes("/manifest"))
        return new Promise<Response>((resolve) => manifestWaits.push(resolve));
      return new Response("missing", { status: 404 });
    };
    await boot(refUrl({ source_id: "source", source_revision: "a", evidence_revision: "b" }));
    await waitForManifest(manifestWaits);
    for (const root of roots.list) root.unmount();
    await act(async () => {
      manifestWaits.shift()?.(json(manifest()));
    });
  });
});

async function waitForManifest(queue: Array<(value: Response) => void>) {
  for (let i = 0; i < 20 && queue.length === 0; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}
