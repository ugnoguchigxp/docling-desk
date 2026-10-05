import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type {
  ExplanationOverview,
  ExplanationResult,
  ExplanationUnit,
  Job,
} from "../lib/types";
import { useExplanation } from "./Explanation";

beforeEach(() => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function doneJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job",
    filename: "file.pdf",
    folder_id: null,
    state: "success",
    created: 1,
    duration: null,
    pages: 2,
    tables: 1,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: null,
    slide_layout: false,
    ...overrides,
  };
}
function unit(overrides: Partial<ExplanationUnit> = {}): ExplanationUnit {
  return {
    id: "u1",
    kind: "page",
    number: 1,
    name: "導入",
    available: false,
    state: { state: "uncreated", latest_version_id: "v1" },
    ...overrides,
  };
}
function overview(
  units: ExplanationUnit[],
  overrides: Partial<ExplanationOverview> = {},
): ExplanationOverview {
  return {
    units,
    source_error: null,
    profile: { web_provider: "duckduckgo" },
    enabled: true,
    configuration_error: null,
    ...overrides,
  };
}
const rich: ExplanationResult = {
  source_match: false,
  result: {
    created_at: "2024-01-02T03:04:05.000Z",
    extraction_state: "partial",
    unlocated_count: 2,
    source: {
      kind: "page",
      excluded_pictures: 3,
      blocks: [{ id: "b1", pages: [1], text: "保存された原文" }],
      tables: [
        {
          ref: "#/tables/0",
          label: "売上",
          source_label: "",
          pages: [],
          columns: 2,
          rows: [
            ["見出し", "値"],
            ["A", "1"],
          ],
          header_rows: 1,
          merged: true,
        },
        {
          ref: "#/tables/1",
          label: "単純",
          source_label: "",
          pages: [],
          columns: 1,
          rows: [["セル"]],
          header_rows: 0,
          merged: false,
        },
      ],
    },
    local_search: { queries: ["用語"], evidence: [] },
    web_search: {
      queries: ["用語"],
      status: "mystery",
      evidence: [
        {
          id: "e1",
          kind: "web",
          title: "公開ページ",
          url: "https://example.com/a",
          pages: [],
          text: "本文",
        },
        {
          id: "e2",
          kind: "web",
          title: "",
          url: "http://example.com/b",
          pages: [],
          text: "本文",
        },
        {
          id: "e3",
          kind: "web",
          title: "危険",
          url: "javascript:alert(1)",
          pages: [],
          text: "本文",
        },
        {
          id: "e4",
          kind: "web",
          title: "不正",
          url: "not a url",
          pages: [],
          text: "本文",
        },
        {
          id: "e5",
          kind: "local",
          title: "内部",
          url: "https://example.com/c",
          pages: [],
          text: "本文",
        },
      ],
    },
    explanation: {
      sections: [
        { title: "", text: "本文です", source_ids: [], evidence_ids: [] },
        { title: "背景", text: "詳しい内容", source_ids: ["s"], evidence_ids: [] },
      ],
      glossary: [{ term: "用語", definition: "意味" }],
      supplements: [
        {
          title: "補足",
          text: "追加情報",
          evidence_ids: ["e1", "e1", "e2", "e3", "e4", "e5", "missing"],
        },
      ],
      limitations: ["制約があります"],
    },
  },
};
const plain: ExplanationResult = {
  source_match: true,
  result: {
    created_at: "2024-05-01T00:00:00.000Z",
    extraction_state: "complete",
    unlocated_count: 0,
    source: {
      kind: "document",
      excluded_pictures: 0,
      blocks: [],
      tables: [],
    },
    local_search: { queries: [], evidence: [] },
    web_search: { queries: [], status: "success", evidence: [] },
    explanation: {
      sections: [{ title: "要約", text: "短い解説", source_ids: [], evidence_ids: [] }],
      glossary: [],
      supplements: [],
      limitations: ["制限"],
    },
  },
};

function renderExplanation(
  job: Job,
  initial: number | null,
  handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
) {
  const fetchMock = vi.fn(handler);
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function View() {
    const [page, setPage] = useState(initial);
    const explanation = useExplanation(job, page);
    return (
      <>
        <button type="button" onClick={() => setPage(2)}>
          次のページ
        </button>
        <button type="button" onClick={() => setPage(1)}>
          先頭のページ
        </button>
        {explanation.controls}
        {explanation.panel}
        <div data-testid="downloads">{explanation.downloads}</div>
        <p data-testid="disclosure">{explanation.disclosure}</p>
      </>
    );
  }
  const view = render(
    <QueryClientProvider client={client}>
      <View />
    </QueryClientProvider>,
  );
  return { ...view, client, fetchMock };
}
async function loaded(title: string) {
  const button = screen.getByRole("button", { name: "わかりやすく解説" });
  await waitFor(() => expect(button).toHaveAttribute("title", title));
  return button;
}

it("shows a saved explanation, downloads, and closes the panel", async () => {
  const posts: unknown[] = [];
  renderExplanation(doneJob(), 1, async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return Response.json({});
    }
    if (/\/explanations\/[^/]+$/.test(url)) return Response.json(rich);
    return Response.json(
      overview([
        unit({
          available: true,
          state: { state: "completed", latest_version_id: "v1" },
        }),
      ]),
    );
  });
  const open = await loaded("現在のページ 1の解説");
  expect(screen.getByTestId("disclosure")).toHaveTextContent(
    "duckduckgoで一般的な用語を検索",
  );
  fireEvent.click(open);
  expect(await screen.findByText("本文です")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "詳しい解説" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "背景" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "用語" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "公開ページ" })).toHaveAttribute(
    "href",
    "https://example.com/a",
  );
  expect(screen.getByRole("link", { name: "補足を詳しく読む" })).toHaveAttribute(
    "href",
    "http://example.com/b",
  );
  expect(screen.getByText("保存された原文")).toBeInTheDocument();
  expect(screen.getByText(/原文の抽出は部分成功です/)).toBeInTheDocument();
  expect(screen.getByText("mystery", { exact: false })).toBeInTheDocument();
  expect(
    screen.getByText("結合セルの範囲は原本と保存JSONで確認できます。"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "見出し", hidden: true }),
  ).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "セル", hidden: true })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "解説を保存（Markdown）" })).toHaveAttribute(
    "href",
    "/api/jobs/job/explanations/u1?download=markdown",
  );
  expect(screen.getByRole("link", { name: "解説と根拠を保存（JSON）" })).toHaveAttribute(
    "href",
    "/api/jobs/job/explanations/u1?download=json",
  );
  fireEvent.click(screen.getByRole("button", { name: "解説を作り直す" }));
  await waitFor(() =>
    expect(posts).toEqual([{ unit_id: "u1", force: true }]),
  );
  fireEvent.keyDown(document.getElementById("explanationPanel")!, { key: "a" });
  fireEvent.keyDown(document.getElementById("explanationPanel")!, { key: "Escape" });
  expect(document.getElementById("explanationPanel")).toHaveAttribute("hidden");
  fireEvent.click(open);
  fireEvent.click(document.getElementById("explainClose")!);
  expect(document.activeElement).toBe(open);
});

it("shows a matched source without supplements and a disabled web provider", async () => {
  renderExplanation(doneJob(), 1, async (input) => {
    if (/\/explanations\/[^/]+$/.test(String(input))) return Response.json(plain);
    return Response.json(
      overview(
        [
          unit({
            kind: "document",
            name: undefined,
            available: true,
            state: { state: "completed", latest_version_id: "v2" },
          }),
        ],
        { profile: { web_provider: "disabled" } },
      ),
    );
  });
  const open = await loaded("文書全体の解説");
  expect(screen.getByTestId("disclosure")).toHaveTextContent("Web補足: 無効");
  fireEvent.click(open);
  expect(await screen.findByText("短い解説")).toBeInTheDocument();
  expect(screen.getByText("本文を確認済み", { exact: false })).toBeInTheDocument();
  expect(screen.queryByText("保存された原文")).toBeNull();
  expect(screen.queryByText("原文の抽出は部分成功です。")).toBeNull();
  expect(screen.queryByRole("heading", { name: "用語の説明" })).toBeNull();
});

it("creates a missing explanation and reports a failed retry", async () => {
  const posts: unknown[] = [];
  let fail = false;
  renderExplanation(doneJob(), 1, async (_input, init) => {
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      if (fail) return Response.json({ detail: "生成に失敗" }, { status: 500 });
      return Response.json({});
    }
    return Response.json(overview([unit()]));
  });
  fireEvent.click(await loaded("現在のページ 1の解説"));
  await waitFor(() => expect(posts).toEqual([{ unit_id: "u1", force: false }]));
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "解説を再試行", hidden: true }));
  expect(await screen.findByText("生成に失敗")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "次のページ" }));
  expect(screen.queryByText("生成に失敗")).toBeNull();
});

it("skips generation when explanation is locked or not allowed", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const posts: unknown[] = [];
  const { fetchMock } = renderExplanation(doneJob(), 1, async (_input, init) => {
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      await gate;
      return Response.json({});
    }
    return Response.json(overview([unit({ state: { state: "failed" } })]));
  });
  const open = await loaded("現在のページ 1の解説");
  const retry = screen.getByRole("button", { name: "解説を再試行", hidden: true });
  fireEvent.click(retry);
  fireEvent.click(retry);
  fireEvent.click(open);
  await waitFor(() => expect(posts).toEqual([{ unit_id: "u1", force: false }]));
  release();
  await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));
});

it("does not generate when the feature, source, or storage is blocked", async () => {
  const cases: ExplanationOverview[] = [
    overview([unit()], { enabled: false }),
    overview([unit({ state: { state: "failed" } })], { source_error: "原文がありません" }),
    overview([unit({ state: { state: "failed" } })], {
      configuration_error: "設定がありません",
    }),
    overview([unit({ storage_error: "保存できません", state: { state: "failed" } })]),
  ];
  for (const data of cases) {
    cleanup();
    const posts: unknown[] = [];
    renderExplanation(doneJob({ id: data.enabled ? "blocked" : "off" }), 1, async (input, init) => {
      if (init?.method === "POST") {
        posts.push(JSON.parse(String(init.body)));
        return Response.json({});
      }
      if (String(input).endsWith("/explanations")) return Response.json(data);
      return Response.json({ result: null, source_match: false });
    });
    await waitFor(() =>
      expect(screen.getByTestId("disclosure")).not.toBeEmptyDOMElement(),
    );
    fireEvent.click(document.getElementById("explainRetry")!);
    fireEvent.click(document.getElementById("explainRegenerate")!);
    expect(posts).toEqual([]);
  }
});

it("describes failed, stale, active, and inapplicable units", async () => {
  const failed = unit({
    available: false,
    stale: true,
    warning: "注意",
    state: {
      state: "failed",
      stage: "review",
      error: "生成エラー",
      review_issues: ["1", "2", "3", "4", "5", "6"],
    },
  });
  renderExplanation(doneJob(), 1, async (input) => {
    if (/\/explanations\/[^/]+$/.test(String(input)))
      return Response.json({ detail: "結果がありません" }, { status: 500 });
    return Response.json(overview([failed]));
  });
  fireEvent.click(await loaded("現在のページ 1の解説"));
  const status = await screen.findByText(/生成エラー/);
  expect(status).toHaveTextContent("review");
  expect(status).toHaveTextContent("注意");
  expect(status).toHaveTextContent("原文が更新されています。再試行できます。");
  expect(status.textContent).toMatch(/1\n2\n3\n4\n5/);
  expect(status.textContent).not.toContain("6");

  cleanup();
  renderExplanation(doneJob({ id: "stale-saved" }), 1, async (input) => {
    if (/\/explanations\/[^/]+$/.test(String(input))) return Response.json(plain);
    return Response.json(
      overview([
        unit({
          available: true,
          stale: true,
          state: { state: "completed", latest_version_id: "v9" },
        }),
      ]),
    );
  });
  fireEvent.click(await loaded("現在のページ 1の解説"));
  expect(
    await screen.findByText("原文が更新されています。保存時の解説を表示します。"),
  ).toBeInTheDocument();

  cleanup();
  renderExplanation(doneJob({ id: "queued" }), 1, async () =>
    Response.json(
      overview([
        unit({
          state: { state: "queued", stage: "検索中" },
        }),
        unit({
          id: "u2",
          number: 2,
          state: { state: "not_applicable" },
        }),
      ]),
    ),
  );
  fireEvent.click(await loaded("現在のページ 1の解説"));
  expect(await screen.findByText("検索中")).toBeInTheDocument();
  expect(document.getElementById("explainRetry")).toHaveAttribute("hidden");
  fireEvent.click(screen.getByRole("button", { name: "次のページ" }));
  await waitFor(() =>
    expect(document.getElementById("explainRetry")).toHaveAttribute("hidden"),
  );
});

it("retries a stale inapplicable unit and ignores a stale open", async () => {
  const posts: unknown[] = [];
  renderExplanation(doneJob(), 1, async (_input, init) => {
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return Response.json({ detail: "遅れて失敗" }, { status: 500 });
    }
    return Response.json(
      overview([
        unit({
          stale: true,
          state: { state: "not_applicable" },
        }),
      ]),
    );
  });
  await loaded("現在のページ 1の解説");
  fireEvent.click(screen.getByRole("button", { name: "解説を再試行", hidden: true }));
  await waitFor(() => expect(posts).toEqual([{ unit_id: "u1", force: false }]));
  expect(await screen.findByText(/遅れて失敗/)).toBeInTheDocument();

  cleanup();
  posts.length = 0;
  let hold = false;
  let releaseGet!: () => void;
  let pending = Promise.resolve();
  renderExplanation(doneJob({ id: "race" }), 1, async (_input, init) => {
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return Response.json({});
    }
    if (hold) await pending;
    return Response.json(overview([unit()]));
  });
  fireEvent.click(await loaded("現在のページ 1の解説"));
  await waitFor(() => expect(posts).toEqual([{ unit_id: "u1", force: false }]));
  posts.length = 0;
  hold = true;
  pending = new Promise<void>((resolve) => {
    releaseGet = resolve;
  });
  fireEvent.click(screen.getByRole("button", { name: "わかりやすく解説" }));
  fireEvent.click(screen.getByRole("button", { name: "次のページ" }));
  releaseGet();
  await act(async () => {
    await Promise.resolve();
  });
  expect(posts).toEqual([]);
});

it("drops an error when the page changes during creation", async () => {
  const posts: unknown[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  renderExplanation(doneJob(), 1, async (_input, init) => {
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      await gate;
      return Response.json({ detail: "ページが変わった" }, { status: 500 });
    }
    return Response.json(overview([unit()]));
  });
  fireEvent.click(await loaded("現在のページ 1の解説"));
  await waitFor(() => expect(posts).toHaveLength(1));
  fireEvent.click(screen.getByRole("button", { name: "次のページ" }));
  release();
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.queryByText("ページが変わった")).toBeNull();
});

it("refetches when the browser comes online and shows overview errors", async () => {
  const fetchMock = vi.fn(async () =>
    Response.json({ detail: "状態を確認できません" }, { status: 500 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function View({ current }: { current: number | null }) {
    const explanation = useExplanation(doneJob(), current);
    return (
      <>
        {explanation.controls}
        {explanation.panel}
        <p data-testid="disclosure">{explanation.disclosure}</p>
      </>
    );
  }
  const view = render(
    <QueryClientProvider client={client}>
      <View current={1} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/状態の確認を再試行しています/)).toBeInTheDocument();
  expect(screen.getByTestId("disclosure")).toBeEmptyDOMElement();
  expect(screen.getByRole("button", { name: "わかりやすく解説" })).toHaveAttribute(
    "title",
    "現在のページ・スライド・シートを確認しています。",
  );
  const before = fetchMock.mock.calls.length;
  window.dispatchEvent(new Event("online"));
  await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
  view.rerender(
    <QueryClientProvider client={client}>
      <View current={null} />
    </QueryClientProvider>,
  );
  view.unmount();
  client.clear();
});

it("keeps the control disabled until a document is ready", () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function View() {
    const explanation = useExplanation(doneJob({ state: "running" }), 1);
    return explanation.controls;
  }
  render(
    <QueryClientProvider client={client}>
      <View />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button", { name: "わかりやすく解説" })).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalled();
  client.clear();
});

it("opens a saved explanation when generation is disabled", async () => {
  renderExplanation(
    doneJob({ id: "saved-only" }),
    1,
    async (input) => {
      if (/\/explanations\/[^/]+$/.test(String(input)))
        return Response.json({ result: null, source_match: true });
      return Response.json(
        overview(
          [
            unit({
              available: true,
              state: { state: "completed", latest_version_id: "v3" },
            }),
          ],
          { enabled: false, profile: {} },
        ),
      );
    },
  );
  const open = await loaded("現在のページ 1の解説");
  expect(open).toBeEnabled();
  expect(screen.getByTestId("disclosure")).toHaveTextContent("未設定");
  fireEvent.click(open);
  expect(document.getElementById("explanationText")).toBeEmptyDOMElement();
});
