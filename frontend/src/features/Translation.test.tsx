import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Job, Language, TranslationOverview } from "../lib/types";
import { useTranslation } from "./Translation";

const dialogMethods = ["showModal", "close"] as const;
const originalDialogMethods = dialogMethods.map((name) =>
  Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name),
);
beforeEach(() => {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value() {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value() {
        this.removeAttribute("open");
      },
    },
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dialogMethods.forEach((name, index) => {
    const original = originalDialogMethods[index];
    if (original)
      Object.defineProperty(HTMLDialogElement.prototype, name, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  });
});

it("uses refreshed units for selection and retains checkbox changes before submitting", async () => {
  // jsdom has dialog elements but does not implement their browser methods.
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value() {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value() {
        this.removeAttribute("open");
      },
    },
  });
  const language = { state: "untranslated", available: false, stale: false };
  const units = [1, 2].map((number) => ({
    id: `slide-${number}`,
    kind: "slide",
    number,
    mode: "overlay",
    excluded_count: 0,
    segments_count: 1,
    languages: { en: language, ja: language },
  }));
  let reads = 0;
  const submitted: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        submitted.push(JSON.parse(String(init.body)));
        return Response.json({});
      }
      return Response.json({
        units: ++reads === 1 ? [] : units,
        unlocated_count: 0,
        profile: { provider: "openai" },
        scheduling: { interval_seconds: 0 },
      });
    }),
  );
  function Preview() {
    const translation = useTranslation(
      { id: "synthetic", state: "success" } as Job,
      2,
      "original",
      () => {},
    );
    return (
      <>
        {translation.controls}
        {translation.dialog}
      </>
    );
  }
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <Preview />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(cache.getQueryData(["translations", "synthetic"])).toBeDefined(),
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "翻訳" }));
  await user.selectOptions(
    await screen.findByRole("combobox", { name: "翻訳する範囲" }),
    "selected",
  );
  const second = screen.getByRole("checkbox", {
    name: "スライド 2",
  });
  expect(second).toBeChecked();
  await user.click(second);
  expect(second).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "翻訳を開始" }));
  expect(screen.getByRole("status")).toHaveTextContent("範囲を選択");
  expect(submitted).toEqual([]);
  const first = screen.getByRole("checkbox", { name: "スライド 1" });
  // Rapid selections may share a React render batch; retain both event values.
  act(() => {
    fireEvent.click(first);
    fireEvent.click(second);
  });
  expect(first).toBeChecked();
  expect(second).toBeChecked();
  await user.click(second);
  await user.click(
    screen.getByRole("checkbox", { name: "保存済みの単位も再翻訳する" }),
  );
  await user.click(screen.getByRole("button", { name: "翻訳を開始" }));
  await waitFor(() =>
    expect(submitted).toEqual([
      {
        target_language: "en",
        unit_ids: ["slide-1"],
        force: true,
        interval_seconds: 0,
      },
    ]),
  );
  cache.clear();
});

const idle = {
  state: "untranslated",
  available: false,
  stale: false,
  error: null,
  result_created_at: null,
};
function overview(
  overrides: Partial<TranslationOverview> = {},
): TranslationOverview {
  return {
    units: [],
    unlocated_count: 0,
    profile: { provider: "openai" },
    configuration_error: null,
    scheduling: { interval_seconds: 60 },
    ...overrides,
  };
}
function renderTranslation(
  data: TranslationOverview,
  props: { current?: number; language?: Language; state?: Job["state"] } = {},
) {
  const posts: unknown[] = [];
  let panel: { available: boolean; texts: string[] } = {
    available: true,
    texts: ["Hello", "World"],
  };
  let failGet = false;
  let failPost = false;
  let gate: Promise<void> | null = null;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (gate) await gate;
    const url = String(input);
    if (init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      if (failPost)
        return Response.json({ detail: "翻訳できません" }, { status: 500 });
      return Response.json({});
    }
    if (failGet) return Response.json({ detail: "確認できません" }, { status: 500 });
    if (/\/translations\/(en|ja)\//.test(url)) return Response.json(panel);
    return Response.json(data);
  });
  vi.stubGlobal("fetch", fetchMock);
  const onLanguage = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Preview({
    current = props.current ?? 1,
    language = props.language ?? "original",
  }: {
    current?: number;
    language?: Language;
  }) {
    const translation = useTranslation(
      { id: "synthetic", state: props.state ?? "success" } as Job,
      current,
      language,
      onLanguage,
    );
    return (
      <>
        {translation.controls}
        {translation.content}
        {translation.dialog}
        <div data-testid="downloads">{translation.downloads}</div>
        <div data-testid="revision">{translation.revision}</div>
      </>
    );
  }
  const view = render(
    <QueryClientProvider client={cache}>
      <Preview />
    </QueryClientProvider>,
  );
  return {
    ...view,
    cache,
    posts,
    onLanguage,
    fetchMock,
    setPanel(next: { available: boolean; texts: string[] }) {
      panel = next;
    },
    setFailGet(value: boolean) {
      failGet = value;
    },
    setFailPost(value: boolean) {
      failPost = value;
    },
    hold(promise: Promise<void> | null) {
      gate = promise;
    },
    rerenderTranslation(current: number, language: Language) {
      view.rerender(
        <QueryClientProvider client={cache}>
          <Preview current={current} language={language} />
        </QueryClientProvider>,
      );
    },
  };
}

it("switches language and explains active, stale, and unavailable units", async () => {
  const soon = new Date(Date.now() + 5000).toISOString();
  const data = overview({
    unlocated_count: 4,
    profile: { provider: "azure_openai" },
    units: [
      {
        id: "page-1",
        kind: "page",
        number: 1,
        mode: "panel",
        excluded_count: 2,
        segments_count: 0,
        languages: {
          en: {
            ...idle,
            state: "waiting",
            available: true,
            stale: true,
            error: "訳に失敗",
            result_created_at: "t1",
            wait_reason: "rate_limit",
            next_attempt_at: soon,
          },
          ja: { ...idle, state: "running", available: true, result_created_at: "t2" },
        },
      },
      {
        id: "slide-2",
        kind: "slide",
        number: 2,
        mode: "overlay",
        excluded_count: 0,
        segments_count: 3,
        languages: {
          en: { ...idle, state: "queued" },
          ja: {
            ...idle,
            state: "completed",
            available: true,
            result_created_at: "t3",
          },
        },
      },
    ],
  });
  const view = renderTranslation(data, { language: "en" });
  expect(await screen.findByText(/翻訳処理中/)).toHaveTextContent(/残り 2単位/);
  expect(screen.getByText(/レート制限のため/)).toHaveTextContent(/約\d+秒/);
  expect(screen.getByText(/原文が更新されています/)).toBeInTheDocument();
  expect(screen.getByText(/訳に失敗/)).toBeInTheDocument();
  expect(screen.getByText(/文字セル 2件/)).toBeInTheDocument();
  expect(screen.getByText(/翻訳対象の文字がありません/)).toBeInTheDocument();
  expect(screen.getByText(/ページ位置のない要素 4件/)).toBeInTheDocument();
  expect(await screen.findByText("Hello")).toBeInTheDocument();
  expect(screen.getByText("World")).toBeInTheDocument();
  expect(screen.getByTestId("revision")).toHaveTextContent("t1");
  expect(screen.getByRole("link", { name: /英訳 ページ 1/ })).toHaveAttribute(
    "href",
    "/api/jobs/synthetic/translations/en/page-1?download=true",
  );
  expect(screen.getByRole("link", { name: /日本語訳 スライド 2/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "訳文を閉じる" }));
  expect(view.onLanguage).toHaveBeenCalledWith("original");

  data.units[0].languages.en.wait_reason = "retry";
  view.cache.setQueryData(["translations", "synthetic"], structuredClone(data));
  expect(await screen.findByText(/再試行まで/)).toBeInTheDocument();
  data.units[0].languages.en.wait_reason = "manual";
  data.units[0].languages.en.next_attempt_at = undefined;
  view.cache.setQueryData(["translations", "synthetic"], structuredClone(data));
  expect(await screen.findByText(/次の翻訳まで/)).toBeInTheDocument();

  view.rerenderTranslation(2, "en");
  expect(await screen.findByText(/サムネイルは原文です/)).toBeInTheDocument();
  expect(screen.getByText(/順番待ちです/)).toBeInTheDocument();
  data.units[1].languages.en.state = "custom";
  view.cache.setQueryData(["translations", "synthetic"], structuredClone(data));
  expect(await screen.findByText(/customです/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("文書の言語"), {
    target: { value: "ja" },
  });
  expect(view.onLanguage).toHaveBeenCalledWith("ja");
  view.cache.clear();
});

it("submits the current document, the whole file, and a failed request", async () => {
  const data = overview({
    scheduling: undefined,
    units: [
      {
        id: "doc",
        kind: "document",
        number: 1,
        mode: "overlay",
        excluded_count: 0,
        segments_count: 1,
        languages: { en: idle, ja: idle },
      },
    ],
  });
  const view = renderTranslation(data, { language: "ja" });
  await screen.findByRole("button", { name: "翻訳" });
  fireEvent.click(screen.getByRole("button", { name: "翻訳" }));
  expect(await screen.findByText(/文書全体を翻訳します/)).toBeInTheDocument();
  expect(screen.getByLabelText("翻訳先")).toHaveValue("ja");
  expect(screen.getByLabelText("次の翻訳までの間隔（秒）")).toHaveValue(60);
  fireEvent.change(screen.getByLabelText("翻訳先"), { target: { value: "en" } });
  fireEvent.change(screen.getByLabelText("次の翻訳までの間隔（秒）"), {
    target: { value: "15" },
  });
  fireEvent.click(screen.getByRole("button", { name: "翻訳を開始" }));
  await waitFor(() =>
    expect(view.posts).toEqual([
      {
        target_language: "en",
        unit_ids: ["doc"],
        force: false,
        interval_seconds: 15,
      },
    ]),
  );
  expect(view.onLanguage).toHaveBeenCalledWith("en");

  fireEvent.click(screen.getByRole("button", { name: "翻訳" }));
  fireEvent.change(await screen.findByLabelText("翻訳する範囲"), {
    target: { value: "all" },
  });
  fireEvent.click(screen.getByRole("button", { name: "翻訳を開始" }));
  await waitFor(() => expect(view.posts[1]).toMatchObject({ unit_ids: null }));

  view.setFailPost(true);
  fireEvent.click(screen.getByRole("button", { name: "翻訳" }));
  fireEvent.click(await screen.findByRole("button", { name: "翻訳を開始" }));
  expect(await screen.findByText("翻訳できません")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
  expect(screen.queryByText(/文書全体を翻訳します/)).not.toBeVisible();
  view.cache.clear();
});

it("keeps the dialog locked while a translation is submitted", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const data = overview({
    configuration_error: null,
    units: [
      {
        id: "page-1",
        kind: "page",
        number: 1,
        mode: "overlay",
        excluded_count: 0,
        segments_count: 1,
        languages: { en: idle, ja: idle },
      },
    ],
  });
  const view = renderTranslation(data);
  const button = await screen.findByRole("button", { name: "翻訳" });
  await waitFor(() => expect(button).toBeEnabled());
  let refetch!: () => void;
  view.hold(
    new Promise<void>((resolve) => {
      refetch = resolve;
    }),
  );
  fireEvent.click(button);
  fireEvent.click(button);
  refetch();
  const submit = await screen.findByRole("button", { name: "翻訳を開始" });
  expect(screen.getByText(/1ページずつ順番に翻訳します/)).toBeVisible();
  view.hold(gate);
  fireEvent.click(submit);
  fireEvent.click(submit);
  fireEvent(
    document.getElementById("translationDialog")!,
    new Event("cancel", { bubbles: true, cancelable: true }),
  );
  expect(screen.getByText(/1ページずつ順番に翻訳します/)).toBeVisible();
  release();
  await waitFor(() => expect(view.posts).toHaveLength(1));
  view.cache.clear();
});

it("reports a failed refresh and disables translation until the job is done", async () => {
  const view = renderTranslation(overview());
  await waitFor(() =>
    expect(view.cache.getQueryData(["translations", "synthetic"])).toBeDefined(),
  );
  view.setFailGet(true);
  fireEvent.click(screen.getByRole("button", { name: "翻訳" }));
  expect(await screen.findByText("確認できません")).toBeInTheDocument();
  view.cache.clear();
  cleanup();

  const pending = vi.fn(() => new Promise(() => undefined));
  vi.stubGlobal("fetch", pending);
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Pending() {
    const translation = useTranslation(
      { id: "later", state: "queued" } as Job,
      1,
      "original",
      () => {},
    );
    return translation.controls;
  }
  render(
    <QueryClientProvider client={cache}>
      <Pending />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button", { name: "翻訳" })).toBeDisabled();
  expect(pending).not.toHaveBeenCalled();
  cache.clear();
});

it("does not submit when no unit is loaded and shows configuration errors", async () => {
  const view = renderTranslation(
    overview({ configuration_error: "APIキーがありません" }),
  );
  await screen.findByRole("button", { name: "翻訳" });
  fireEvent.click(screen.getByRole("button", { name: "翻訳" }));
  const submit = await screen.findByRole("button", { name: "翻訳を開始" });
  expect(submit).toBeDisabled();
  expect(screen.getByText("APIキーがありません")).toBeInTheDocument();
  fireEvent.click(submit);
  expect(view.posts).toEqual([]);
  view.cache.removeQueries({ queryKey: ["translations", "synthetic"] });
  fireEvent.click(submit);
  expect(view.posts).toEqual([]);
  view.cache.clear();
});

it("shows a panel error and an unavailable panel", async () => {
  const data = overview({
    units: [
      {
        id: "page-1",
        kind: "page",
        number: 1,
        mode: "panel",
        excluded_count: 0,
        segments_count: 2,
        languages: {
          en: {
            ...idle,
            state: "completed",
            available: true,
            result_created_at: "saved",
          },
          ja: idle,
        },
      },
    ],
  });
  const view = renderTranslation(data, { language: "en" });
  view.setPanel({ available: false, texts: [] });
  await screen.findByRole("button", { name: "翻訳" });
  expect(screen.queryByText("Hello")).toBeNull();
  view.setFailGet(true);
  view.cache.invalidateQueries({ queryKey: ["translation-result"] });
  expect(await screen.findByText(/HTTP 500|確認できません/)).toBeInTheDocument();
  view.cache.clear();
});
