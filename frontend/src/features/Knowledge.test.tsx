import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Route } from "../lib/route";
import { KnowledgeSearch, ModeMenu, Wiki } from "./Knowledge";

const dialogMethods = ["showModal", "close"] as const;
const originalDialogMethods = dialogMethods.map((name) =>
  Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name),
);
const originalScrollIntoView = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "scrollIntoView",
);
const originalScrollTo = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "scrollTo",
);
const scrollIntoView = vi.fn();
const scrollTo = vi.fn();
const clients: QueryClient[] = [];

type FetchHandler = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Response | Promise<Response>;

let handle: FetchHandler = () => {
  throw new Error("unexpected fetch");
};
let fetchMock = vi.fn<FetchHandler>();

beforeEach(() => {
  // jsdom implements <dialog> elements without showModal/close.
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
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: scrollTo,
  });
  scrollIntoView.mockClear();
  scrollTo.mockClear();
  document.title = "preset";
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 0;
  });
  handle = () => {
    throw new Error("unexpected fetch");
  };
  fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    Promise.resolve(handle(input, init)),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((query) => query.clear());
  dialogMethods.forEach((name, index) => {
    const original = originalDialogMethods[index];
    if (original) Object.defineProperty(HTMLDialogElement.prototype, name, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  });
  if (originalScrollIntoView)
    Object.defineProperty(
      HTMLElement.prototype,
      "scrollIntoView",
      originalScrollIntoView,
    );
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  if (originalScrollTo)
    Object.defineProperty(HTMLElement.prototype, "scrollTo", originalScrollTo);
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollTo");
  vi.unstubAllGlobals();
  document.title = "";
});

function queryClient() {
  const query = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false },
    },
  });
  clients.push(query);
  return query;
}

function makeRoute(partial: Partial<Route> = {}): Route {
  return {
    mode: "wiki",
    source: null,
    section: null,
    unit: null,
    folder: null,
    job: null,
    view: "preview",
    ...partial,
  };
}

function requestUrl(input: RequestInfo | URL) {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function methodOf(init?: RequestInit) {
  return init?.method ?? "GET";
}

function jsonBody(init?: RequestInit) {
  return JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
}

function json(body: unknown, status = 200) {
  return Response.json(body, { status });
}

function problem(detail: string, status = 400) {
  return json({ detail }, status);
}

function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function settle(resolve: (value: Response) => void, response: Response) {
  await act(async () => {
    resolve(response);
  });
}

function file(name: string, relativePath?: string) {
  const value = new File(["hello"], name, { type: "text/plain" });
  if (relativePath)
    Object.defineProperty(value, "webkitRelativePath", { value: relativePath });
  return value;
}

function chooseFiles(input: HTMLElement, files: File[]) {
  Object.defineProperty(input, "files", { configurable: true, value: files });
  fireEvent.change(input);
}

function emitTransfer(
  target: Element,
  type: "dragOver" | "drop",
  types: string[],
  files: File[] = [],
) {
  fireEvent[type](target, { dataTransfer: { types, files } });
}

function renderWiki(initial: { route: Route; hidden: boolean }) {
  const navigate = vi.fn();
  const query = queryClient();
  const state = {
    hidden: initial.hidden,
    route: { ...initial.route },
  };
  const ui = () => (
    <QueryClientProvider client={query}>
      <Wiki
        route={state.route}
        navigate={navigate}
        navigation={<span>画面メニュー</span>}
        hidden={state.hidden}
      />
    </QueryClientProvider>
  );
  const view = render(ui());
  return {
    ...view,
    navigate,
    query,
    state,
    set(next: { route?: Route; hidden?: boolean }) {
      if (next.route) state.route = next.route;
      if (next.hidden !== undefined) state.hidden = next.hidden;
      view.rerender(ui());
    },
  };
}

function renderSearch(initial: { open: boolean; route: Route }) {
  const navigate = vi.fn();
  const onClose = vi.fn();
  const query = queryClient();
  const state = { open: initial.open, route: { ...initial.route } };
  const ui = () => (
    <QueryClientProvider client={query}>
      <KnowledgeSearch
        open={state.open}
        onClose={onClose}
        route={state.route}
        navigate={navigate}
      />
    </QueryClientProvider>
  );
  const view = render(ui());
  return {
    ...view,
    navigate,
    onClose,
    query,
    state,
    set(next: { open?: boolean; route?: Route }) {
      if (next.open !== undefined) state.open = next.open;
      if (next.route) state.route = next.route;
      view.rerender(ui());
    },
  };
}

function wikiRegion() {
  return screen.getByRole("region", { name: "Wiki" });
}

function importDialog() {
  const node = document.getElementById("wikiImport");
  if (!(node instanceof HTMLDialogElement)) throw new Error("missing import dialog");
  return node;
}

function deleteDialog() {
  const node = document.querySelector('[aria-labelledby="wikiDeleteTitle"]');
  if (!(node instanceof HTMLDialogElement)) throw new Error("missing delete dialog");
  return node;
}

function searchDialog() {
  const node = document.getElementById("knowledgeSearch");
  if (!(node instanceof HTMLDialogElement)) throw new Error("missing search dialog");
  return node;
}

function areaButton(root: ParentNode, area: string, name: string) {
  const scope = root.querySelector(area);
  if (!(scope instanceof HTMLElement)) throw new Error(`missing ${area}`);
  return within(scope).getByRole("button", { name });
}

const summaries = [
  {
    id: "ja",
    title: "導入",
    namespace: "Guide",
    path: "Intro.md",
    revision: "rev-ja",
    language: "ja",
    translation_group: "g1",
    translation_status: "untranslated",
  },
  {
    id: "en",
    title: "Intro",
    namespace: "Guide",
    path: "intro.en.md",
    revision: "rev-en",
    translation_group: "g1",
    translation_status: "needs_review",
  },
  {
    id: "fr",
    title: "Introduction",
    namespace: "Guide",
    path: "intro.fr.md",
    revision: "rev-fr",
    language: "fr",
    translation_group: "g1",
    translation_status: "translated",
  },
  {
    id: "csv",
    title: "別表",
    namespace: "Other",
    path: "table.csv",
    revision: "rev-csv",
    entry_kind: "csv",
  },
];

const articleHtml = [
  "<p>本文</p>",
  '<h2 id="intro">見出し</h2>',
  '<a href="#intro">hash</a>',
  '<a href="#">top</a>',
  '<a href="https://example.com/out">ext</a>',
  '<a href="/files/x">file</a>',
  '<a href="/?mode=wiki&source=en&section=s2">wiki</a>',
  '<a href="/?mode=library&job=job-9&unit=4">library</a>',
  '<a href="/?job=j0">nounit</a>',
  '<a href="/?mode=wiki&source=z" target="_blank">blank</a>',
  '<a href="/?mode=wiki&source=z" aria-disabled="true">disabled</a>',
  "<a>nohref</a>",
].join("");

const jaArticle = {
  ...summaries[0],
  body: "body",
  html: articleHtml,
  outline: [
    { title: "見出し", anchor: "intro", level: 2 },
    { title: "詳細", anchor: "detail", level: 3 },
  ],
  related_document: { job_id: "job-1", title: "元資料" },
  source_unit: 3,
  fallback_original: "original text",
};

const csvArticle = {
  ...summaries[3],
  body: "csv",
  html: "<p>csv body</p>",
  outline: [],
  related_document: { job_id: "", title: "空の資料" },
  source_unit: 0,
  fallback_original: null,
};

function installWiki(articles: Record<string, object> = { ja: jaArticle, csv: csvArticle }) {
  const imports: FormData[] = [];
  let catalog: { articles: object[]; namespaces: string[] } = {
    articles: summaries,
    namespaces: ["Guide", "Other"],
  };
  let catalogStatus = 200;
  const articleStatus: Record<string, number> = {};
  let importReply: () => Response | Promise<Response> = () => json({ articles: [] });
  let deleteReply: (id: string) => Response | Promise<Response> = () => json({});
  handle = (input, init) => {
    const url = requestUrl(input);
    const method = methodOf(init);
    if (url.endsWith("/api/wiki/catalog") && method === "GET") {
      if (catalogStatus !== 200) return problem("一覧を取得できません", catalogStatus);
      return json(catalog);
    }
    const match = url.match(/\/api\/wiki\/sources\/([^/]+)$/);
    if (match && method === "GET") {
      const id = decodeURIComponent(match[1]);
      if (articleStatus[id]) return problem("記事を読めません", articleStatus[id]);
      const article = articles[id];
      if (!article) return problem("記事を読めません", 404);
      return json(article);
    }
    if (match && method === "DELETE")
      return deleteReply(decodeURIComponent(match[1]));
    if (url.endsWith("/api/wiki/import") && method === "POST") {
      if (init?.body instanceof FormData) imports.push(init.body);
      return importReply();
    }
    throw new Error(`unexpected ${method} ${url}`);
  };
  return {
    imports,
    get catalog() {
      return catalog;
    },
    set catalog(value) {
      catalog = value;
    },
    set catalogStatus(value: number) {
      catalogStatus = value;
    },
    articleStatus,
    set importReply(value: () => Response | Promise<Response>) {
      importReply = value;
    },
    set deleteReply(value: (id: string) => Response | Promise<Response>) {
      deleteReply = value;
    },
  };
}

const wikiHit = {
  chunk_id: "c-wiki",
  source_id: "src-wiki",
  kind: "wiki" as const,
  title: "導入",
  text: "本文A",
  job_id: null,
  unit: 1,
  partial: true,
  locator: { anchor: "intro", heading: "節", refs: ["r1"] },
};
const docHit = {
  chunk_id: "c-doc",
  source_id: "src-doc",
  kind: "document" as const,
  title: "仕様書",
  text: "本文B",
  job_id: "job-2",
  unit: 5,
  partial: false,
  locator: { refs: [] as string[] },
};
const bareWikiHit = {
  ...wikiHit,
  chunk_id: "c-bare",
  title: "付録",
  partial: false,
  locator: { refs: [] as string[] },
};

function task(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    state: "completed",
    progress: 1,
    total: 1,
    output: { results: [] as object[] },
    ...over,
  };
}

function installKnowledge() {
  let statusBody: object = {
    azure_configured: true,
    configuration_error: "",
    chunks: 10,
    embedded: 4,
    cooldown_until: null,
    jobs: [] as object[],
  };
  let statusReply: () => Response | Promise<Response> = () => json(statusBody);
  let retrievalBody = task();
  let retrievalReply: () => Response = () => json(retrievalBody);
  let searchReply: () => Response | Promise<Response> = () => json(retrievalBody);
  let contextReply: () => Response | Promise<Response> = () => json({ text: "根拠" });
  let indexReply: () => Response | Promise<Response> = () => json({});
  let cancelReply: (id: string) => Response | Promise<Response> = () =>
    json(task({ state: "cancelled" }));
  const searches: Record<string, unknown>[] = [];
  const contexts: Record<string, unknown>[] = [];
  handle = (input, init) => {
    const url = requestUrl(input);
    const method = methodOf(init);
    if (url.endsWith("/api/knowledge/status") && method === "GET") return statusReply();
    if (url.endsWith("/api/knowledge/search") && method === "POST") {
      searches.push(jsonBody(init));
      return searchReply();
    }
    if (/\/api\/knowledge\/retrievals\/[^/]+$/.test(url) && method === "GET")
      return retrievalReply();
    if (url.endsWith("/api/knowledge/context") && method === "POST") {
      contexts.push(jsonBody(init));
      return contextReply();
    }
    if (url.endsWith("/api/knowledge/index-jobs") && method === "POST")
      return indexReply();
    const cancel = url.match(/\/api\/knowledge\/tasks\/([^/]+)\/cancel$/);
    if (cancel && method === "POST") return cancelReply(decodeURIComponent(cancel[1]));
    throw new Error(`unexpected ${method} ${url}`);
  };
  return {
    searches,
    contexts,
    get statusBody() {
      return statusBody;
    },
    set statusBody(value: object) {
      statusBody = value;
    },
    set statusReply(value: () => Response | Promise<Response>) {
      statusReply = value;
    },
    get retrievalBody() {
      return retrievalBody;
    },
    set retrievalBody(value: ReturnType<typeof task>) {
      retrievalBody = value;
    },
    set retrievalReply(value: () => Response) {
      retrievalReply = value;
    },
    set searchReply(value: () => Response | Promise<Response>) {
      searchReply = value;
    },
    set contextReply(value: () => Response | Promise<Response>) {
      contextReply = value;
    },
    set indexReply(value: () => Response | Promise<Response>) {
      indexReply = value;
    },
    set cancelReply(value: (id: string) => Response | Promise<Response>) {
      cancelReply = value;
    },
    publish(query: QueryClient, next: ReturnType<typeof task>) {
      retrievalBody = next;
      query.setQueryData(["knowledge-search", next.id], next);
    },
  };
}

describe("ModeMenu", () => {
  it("marks the active mode and opens search", async () => {
    const onMode = vi.fn();
    const onSearch = vi.fn();
    const user = userEvent.setup();
    const view = render(
      <ModeMenu mode="library" onMode={onMode} onSearch={onSearch} />,
    );
    expect(screen.getByRole("button", { name: "資料一覧" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "Wiki" })).not.toHaveAttribute(
      "aria-current",
    );
    await user.click(screen.getByRole("button", { name: "Wiki" }));
    expect(onMode).toHaveBeenCalledWith("wiki");
    await user.click(screen.getByRole("button", { name: "資料一覧" }));
    expect(onMode).toHaveBeenCalledWith("library");
    view.rerender(<ModeMenu mode="wiki" onMode={onMode} onSearch={onSearch} />);
    expect(screen.getByRole("button", { name: "Wiki" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "資料一覧" })).not.toHaveAttribute(
      "aria-current",
    );
    await user.click(screen.getByRole("button", { name: "本文を検索" }));
    expect(onSearch).toHaveBeenCalledOnce();
  });
});

describe("Wiki", () => {
  it("stays idle while hidden and shows an empty catalog", async () => {
    const gate = deferred();
    handle = (input) => {
      const url = requestUrl(input);
      if (url.endsWith("/api/wiki/catalog")) return gate.promise;
      throw new Error(url);
    };
    const view = renderWiki({ route: makeRoute(), hidden: true });
    expect(document.title).toBe("preset");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("記事一覧を読み込み中");
    expect(document.querySelector(".wiki-screen")).toHaveAttribute("hidden");
    view.set({ hidden: false });
    expect(screen.getByText("画面メニュー")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Wiki" })).toBeInTheDocument();
    expect(screen.getByText("記事一覧を読み込み中…")).toBeInTheDocument();
    expect(screen.getByText("Markdownで知識をまとめる")).toBeInTheDocument();
    await settle(gate.resolve, json({ articles: [], namespaces: [] }));
    expect(
      await screen.findByText(
        "記事がありません。Markdownファイルを取り込んでください。",
      ),
    ).toBeInTheDocument();
    expect(document.title).toBe("Wiki · Docling Desk");
    expect(screen.queryByText("記事一覧を読み込み中…")).not.toBeInTheDocument();
  });

  it("shows catalog and article errors", async () => {
    const api = installWiki();
    api.catalogStatus = 500;
    api.articleStatus.missing = 404;
    renderWiki({
      route: makeRoute({ source: "missing" }),
      hidden: false,
    });
    expect(await screen.findByText("一覧を取得できません")).toBeInTheDocument();
    expect(await screen.findByText("記事を読めません")).toBeInTheDocument();
    expect(
      screen.getByText("記事がありません。Markdownファイルを取り込んでください。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Markdownで知識をまとめる")).not.toBeInTheDocument();
  });

  it("filters articles and follows headings, languages, and related documents", async () => {
    installWiki();
    const user = userEvent.setup();
    const base = makeRoute({
      source: "ja",
      section: "intro",
      folder: "folder-1",
      job: "job-x",
    });
    const view = renderWiki({ route: base, hidden: false });
    expect(await screen.findByRole("heading", { name: "見出し" })).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
    expect(document.querySelector(".wiki-body h2")).toHaveAttribute("tabindex", "-1");
    expect(document.title).toBe("導入 · Docling Desk");
    expect(screen.getByText("日本語訳は未公開です。原文を表示しています。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Markdownを保存" })).toHaveAttribute(
      "href",
      "/api/wiki/sources/ja/markdown",
    );
    expect(screen.getByRole("button", { name: /^導入/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("option", { name: "ja（未翻訳）" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "intro.en.md（要確認）" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "fr" })).toBeInTheDocument();

    await user.type(
      screen.getByRole("searchbox", { name: "Wikiの記事名を絞り込む" }),
      "intro",
    );
    expect(screen.queryByRole("button", { name: /別表/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Intro" })).toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox", { name: "Wikiの記事名を絞り込む" }));
    await user.type(
      screen.getByRole("searchbox", { name: "Wikiの記事名を絞り込む" }),
      "___none___",
    );
    expect(
      screen.getByText("記事がありません。Markdownファイルを取り込んでください。"),
    ).toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox", { name: "Wikiの記事名を絞り込む" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Wikiの分類" }), "Other");
    expect(screen.getByRole("button", { name: /別表/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^導入/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Wikiの分類" }), "");
    expect(screen.getByRole("button", { name: /^導入/ })).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "記事の言語" }), "en");
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({ ...base, source: "en", section: null }),
    );
    await user.click(screen.getByRole("button", { name: "関連資料：元資料" }));
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({ ...base, mode: "library", job: "job-1", view: "preview", unit: 3 }),
    );
    const outline = screen.getByRole("navigation", { name: "記事の目次" });
    await user.click(within(outline).getByRole("link", { name: "詳細" }));
    expect(view.navigate).toHaveBeenCalledWith(makeRoute({ ...base, section: "detail" }));

    const body = document.querySelector(".wiki-body");
    if (!(body instanceof HTMLElement)) throw new Error("missing body");
    const clickLink = (name: string, init: MouseEventInit = {}) => {
      view.navigate.mockClear();
      const link = within(body).getByRole("link", { name });
      fireEvent.click(link, init);
    };
    view.navigate.mockClear();
    fireEvent.click(within(body).getByText("本文"));
    expect(view.navigate).not.toHaveBeenCalled();
    clickLink("wiki", { metaKey: true });
    clickLink("wiki", { ctrlKey: true });
    clickLink("wiki", { shiftKey: true });
    clickLink("wiki", { altKey: true });
    clickLink("blank");
    clickLink("disabled");
    clickLink("ext");
    clickLink("file");
    expect(view.navigate).not.toHaveBeenCalled();
    view.navigate.mockClear();
    fireEvent.click(body.querySelector("a:not([href])")!);
    expect(view.navigate).not.toHaveBeenCalled();

    clickLink("hash");
    expect(view.navigate).toHaveBeenCalledWith(makeRoute({ ...base, section: "intro" }));
    clickLink("top");
    expect(view.navigate).toHaveBeenCalledWith(makeRoute({ ...base, section: null }));
    clickLink("wiki");
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({
        ...base,
        mode: "wiki",
        source: "en",
        section: "s2",
        job: null,
        unit: null,
      }),
    );
    clickLink("library");
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({
        ...base,
        mode: "library",
        source: null,
        section: null,
        job: "job-9",
        unit: 4,
      }),
    );
    clickLink("nounit");
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({
        ...base,
        mode: "library",
        source: null,
        section: null,
        job: "j0",
        unit: null,
      }),
    );

    scrollIntoView.mockClear();
    scrollTo.mockClear();
    view.set({ route: makeRoute({ ...base, section: null }) });
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 0 }));
    expect(scrollIntoView).not.toHaveBeenCalled();
    scrollIntoView.mockClear();
    scrollTo.mockClear();
    view.set({ route: makeRoute({ ...base, section: "missing" }) });
    await waitFor(() => expect(document.title).toBe("導入 · Docling Desk"));
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(scrollTo).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /別表/ }));
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({ ...base, source: "csv", section: null }),
    );
    view.set({ route: makeRoute({ ...base, source: "csv", section: null }) });
    expect(await screen.findByRole("link", { name: "CSVを保存" })).toHaveAttribute(
      "href",
      "/api/wiki/sources/csv/markdown",
    );
    expect(screen.queryByRole("combobox", { name: "記事の言語" })).not.toBeInTheDocument();
    expect(screen.queryByText("日本語訳は未公開です。原文を表示しています。")).not.toBeInTheDocument();
    view.navigate.mockClear();
    await user.click(screen.getByRole("button", { name: "関連資料：空の資料" }));
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({
        ...base,
        mode: "library",
        source: "csv",
        section: null,
        job: null,
        view: "preview",
        unit: 1,
      }),
    );
  });

  it("deletes the open article and closes the confirmation", async () => {
    const api = installWiki();
    const user = userEvent.setup();
    const base = makeRoute({ source: "ja", section: "intro" });
    const view = renderWiki({ route: base, hidden: false });
    expect(await screen.findByRole("heading", { name: "見出し" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "削除する", hidden: true }));
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/wiki\/sources\/ja$/),
      expect.objectContaining({ method: "DELETE" }),
    );

    await user.click(screen.getByRole("button", { name: "記事を削除" }));
    await user.click(areaButton(deleteDialog(), ".dialog-heading", "閉じる"));
    await waitFor(() => expect(deleteDialog().open).toBe(false));
    await user.click(screen.getByRole("button", { name: "記事を削除" }));
    await user.click(areaButton(deleteDialog(), ".dialog-actions", "閉じる"));
    await waitFor(() => expect(deleteDialog().open).toBe(false));
    await user.click(screen.getByRole("button", { name: "記事を削除" }));
    fireEvent(
      deleteDialog(),
      new Event("cancel", { bubbles: true, cancelable: true }),
    );
    await waitFor(() => expect(deleteDialog().open).toBe(false));

    const denied = deferred();
    api.deleteReply = () => denied.promise;
    await user.click(screen.getByRole("button", { name: "記事を削除" }));
    const remove = within(deleteDialog()).getByRole("button", { name: "削除する" });
    await user.click(remove);
    fireEvent.click(remove);
    await settle(denied.resolve, problem("削除できません"));
    expect((await screen.findAllByText("削除できません")).length).toBeGreaterThan(0);
    expect(deleteDialog().open).toBe(true);

    api.deleteReply = () => json({});
    await user.click(within(deleteDialog()).getByRole("button", { name: "削除する" }));
    expect((await screen.findAllByText("記事を削除しました。")).length).toBeGreaterThan(0);
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({ ...base, source: null, section: null }),
    );
    await waitFor(() => expect(deleteDialog().open).toBe(false));
  });

  it("imports markdown, filters dropped files, and reports import failures", async () => {
    const api = installWiki({ ja: jaArticle });
    api.catalog = { articles: [], namespaces: [] };
    const user = userEvent.setup();
    const view = renderWiki({ route: makeRoute(), hidden: false });
    expect(await screen.findByText("Markdownで知識をまとめる")).toBeInTheDocument();
    const region = wikiRegion();
    const ignored = new Event("dragover", { bubbles: true, cancelable: true });
    Object.assign(ignored, { dataTransfer: { types: ["text/plain"], files: [] } });
    fireEvent(region, ignored);
    expect(ignored.defaultPrevented).toBe(false);
    const allowed = new Event("dragover", { bubbles: true, cancelable: true });
    Object.assign(allowed, { dataTransfer: { types: ["Files"], files: [] } });
    fireEvent(region, allowed);
    expect(allowed.defaultPrevented).toBe(true);
    emitTransfer(region, "drop", ["text/plain"]);
    emitTransfer(region, "drop", ["Files"], [file("wiki-manifest.json")]);
    expect(
      screen.getAllByText(
        "WikiにはMarkdown記事またはCSV目次を取り込んでください。フォルダーは取り込み画面から選べます。",
      ).length,
    ).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    expect(screen.queryAllByText(/WikiにはMarkdown記事またはCSV目次/)).toHaveLength(0);
    expect(importDialog().open).toBe(true);
    expect(screen.getByLabelText("Wikiのフォルダー")).toHaveAttribute(
      "webkitdirectory",
      "",
    );
    expect(screen.getByText("0件を選択")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取り込む" })).toBeDisabled();
    fireEvent.submit(importDialog().querySelector("form")!);
    expect(api.imports).toHaveLength(0);

    await user.click(areaButton(importDialog(), ".dialog-heading", "閉じる"));
    await waitFor(() => expect(importDialog().open).toBe(false));
    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    await user.click(areaButton(importDialog(), ".dialog-actions", "閉じる"));
    await waitFor(() => expect(importDialog().open).toBe(false));
    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    fireEvent(
      importDialog(),
      new Event("cancel", { bubbles: true, cancelable: true }),
    );
    await waitFor(() => expect(importDialog().open).toBe(false));

    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    chooseFiles(screen.getByLabelText("WikiのMarkdownファイル"), [file("notes.txt")]);
    expect(screen.getByText("1件を選択")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取り込む" })).toBeDisabled();
    fireEvent.submit(importDialog().querySelector("form")!);
    expect(api.imports).toHaveLength(0);

    emitTransfer(region, "drop", ["Files"], [file("chapter.md"), file("photo.png")]);
    expect(
      screen.getAllByText(
        "Markdown記事・CSV目次・manifest以外の資料は取り込み対象から除外しました。",
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("1件を選択")).toBeInTheDocument();
    emitTransfer(region, "drop", ["Files"], [file("only.md"), file("pages.jsonl")]);
    expect(screen.queryAllByText(/取り込み対象から除外しました/)).toHaveLength(0);

    await user.upload(screen.getByLabelText("Wikiのフォルダー"), [
      file("keep.md"),
      file("skip.txt"),
      file("wiki-manifest.json"),
      file("pages.jsonl"),
      file("Guide.markdown"),
    ]);
    expect(screen.getByText("4件を選択")).toBeInTheDocument();
    chooseFiles(screen.getByLabelText("WikiのMarkdownファイル"), [
      file("note.md", "root/docs/note.md"),
      file("table.csv"),
      file("wiki-manifest.json"),
      file("pages.jsonl"),
      file("ignore.txt"),
    ]);
    expect(screen.getByText("5件を選択")).toBeInTheDocument();
    await user.clear(screen.getByRole("textbox", { name: "分類名" }));
    await user.type(screen.getByRole("textbox", { name: "分類名" }), "Handbook");

    const gate = deferred();
    api.importReply = () => gate.promise;
    await user.click(screen.getByRole("button", { name: "取り込む" }));
    expect(screen.getByRole("button", { name: "取り込み中…" })).toBeDisabled();
    fireEvent.submit(importDialog().querySelector("form")!);
    emitTransfer(region, "drop", ["Files"], [file("later.md")]);
    expect(
      screen.getAllByText("取り込み中です。完了後に追加してください。").length,
    ).toBeGreaterThan(0);
    expect(api.imports).toHaveLength(1);
    await settle(
      gate.resolve,
      json({
        articles: [
          {
            id: "new",
            title: "新規",
            namespace: "Handbook",
            path: "note.md",
            revision: "1",
          },
        ],
      }),
    );
    expect((await screen.findAllByText("1件の記事を取り込みました。")).length).toBeGreaterThan(
      0,
    );
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({ mode: "wiki", source: "new", section: null }),
    );
    await waitFor(() => expect(importDialog().open).toBe(false));
    const entries = [...api.imports[0].entries()].map(([field, value]) => ({
      field,
      name: value instanceof File ? value.name : String(value),
    }));
    expect(entries).toEqual(
      expect.arrayContaining([
        { field: "namespace", name: "Handbook" },
        { field: "files", name: "docs/note.md" },
        { field: "files", name: "table.csv" },
        { field: "files", name: "ignore.txt" },
        { field: "manifest", name: "wiki-manifest.json" },
        { field: "manifest", name: "pages.jsonl" },
      ]),
    );

    view.navigate.mockClear();
    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    chooseFiles(screen.getByLabelText("WikiのMarkdownファイル"), [file("empty.md")]);
    api.importReply = () => json({ articles: [] });
    await user.click(screen.getByRole("button", { name: "取り込む" }));
    expect((await screen.findAllByText("0件の記事を取り込みました。")).length).toBeGreaterThan(
      0,
    );
    expect(view.navigate).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    chooseFiles(screen.getByLabelText("WikiのMarkdownファイル"), [file("bad.md")]);
    api.importReply = () => problem("取り込めません");
    await user.click(screen.getByRole("button", { name: "取り込む" }));
    expect((await screen.findAllByText("取り込めません")).length).toBeGreaterThan(0);
    expect(importDialog().open).toBe(true);
  });

  it("drops wiki writes that finish after the reader changes", async () => {
    const api = installWiki();
    const user = userEvent.setup();
    const base = makeRoute({ source: "ja" });
    const view = renderWiki({ route: base, hidden: false });
    expect(await screen.findByRole("heading", { name: "見出し" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Markdownを取り込む" }));
    chooseFiles(screen.getByLabelText("WikiのMarkdownファイル"), [file("late.md")]);
    const imported = deferred();
    api.importReply = () => imported.promise;
    await user.click(screen.getByRole("button", { name: "取り込む" }));
    view.set({ route: makeRoute({ source: "csv" }) });
    await settle(
      imported.resolve,
      json({
        articles: [
          { id: "late", title: "遅い", namespace: "Wiki", path: "late.md", revision: "1" },
        ],
      }),
    );
    expect(view.navigate).not.toHaveBeenCalled();
    expect(screen.queryAllByText("1件の記事を取り込みました。")).toHaveLength(0);

    expect(await screen.findByRole("link", { name: "CSVを保存" })).toBeInTheDocument();
    const removed = deferred();
    api.deleteReply = () => removed.promise;
    await user.click(screen.getByRole("button", { name: "記事を削除" }));
    await user.click(within(deleteDialog()).getByRole("button", { name: "削除する" }));
    view.set({ hidden: true });
    await settle(removed.resolve, problem("stale-delete"));
    expect(view.navigate).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("stale-delete");
    expect(document.body.textContent).not.toContain("記事を削除しました。");
  });
});

describe("KnowledgeSearch", () => {
  it("shows index status, search modes, and scope fallbacks", async () => {
    const api = installKnowledge();
    const statusGate = deferred();
    api.statusReply = () => statusGate.promise;
    const user = userEvent.setup();
    const view = renderSearch({
      open: false,
      route: makeRoute({ mode: "library", job: "job-7", folder: "folder-9" }),
    });
    expect(fetchMock).not.toHaveBeenCalled();
    view.set({ open: true });
    expect(screen.getByText("意味検索の準備：0 / 0断片")).toBeInTheDocument();
    expect(
      screen.getByText("Azure未設定。全文検索を利用できます。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "意味検索" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "全文＋意味検索" })).toBeDisabled();

    const ready = {
      azure_configured: true,
      configuration_error: "",
      chunks: 10,
      embedded: 4,
      cooldown_until: null,
      jobs: [],
    };
    api.statusBody = ready;
    await settle(statusGate.resolve, json(ready));
    api.statusReply = () => json(api.statusBody);
    expect(await screen.findByText("意味検索の準備：4 / 10断片")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "意味検索の索引を作成・更新" }),
    ).toBeEnabled();
    expect(screen.getByText("Azureへの通信後は15秒待ちます。")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "意味検索" })).toBeEnabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "検索方法" }), "semantic");
    await user.selectOptions(screen.getByRole("combobox", { name: "検索方法" }), "hybrid");
    await user.selectOptions(screen.getByRole("combobox", { name: "検索対象" }), "document");
    await user.selectOptions(screen.getByRole("combobox", { name: "検索対象" }), "wiki");
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "current");
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "folder");

    view.set({ route: makeRoute({ mode: "library", job: "job-7", folder: null }) });
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "範囲" })).toHaveValue("all"),
    );
    expect(screen.getByRole("option", { name: "現在の資料・記事" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "現在のフォルダー配下" })).toBeDisabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "current");
    view.set({ route: makeRoute({ mode: "library" }) });
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "範囲" })).toHaveValue("all"),
    );
    expect(screen.getByRole("option", { name: "現在の資料・記事" })).toBeDisabled();

    view.set({ route: makeRoute({ mode: "wiki", source: "ja" }) });
    expect(screen.getByRole("option", { name: "現在の資料・記事" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "現在のフォルダー配下" })).toBeDisabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "current");
    view.set({ route: makeRoute({ mode: "wiki" }) });
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "範囲" })).toHaveValue("all"),
    );
    expect(screen.getByRole("option", { name: "現在の資料・記事" })).toBeDisabled();

    api.statusBody = {
      azure_configured: false,
      configuration_error: "",
      chunks: 0,
      embedded: 0,
      cooldown_until: null,
      jobs: [{ id: "done", state: "completed", progress: 1, total: 1, output: {} }],
    };
    await act(async () => {
      await view.query.refetchQueries({ queryKey: ["knowledge-status"] });
    });
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "検索方法" })).toHaveValue("text"),
    );
    expect(
      screen.getByText("Azure未設定。全文検索を利用できます。"),
    ).toBeInTheDocument();
    api.statusBody = {
      azure_configured: false,
      configuration_error: "接続できません",
      chunks: 1,
      embedded: 0,
      cooldown_until: null,
      jobs: [],
    };
    await act(async () => {
      await view.query.refetchQueries({ queryKey: ["knowledge-status"] });
    });
    expect(await screen.findByText("接続できません")).toBeInTheDocument();
    await user.click(areaButton(searchDialog(), ".command-input", "閉じる"));
    expect(view.onClose).toHaveBeenCalled();
    fireEvent(
      searchDialog(),
      new Event("cancel", { bubbles: true, cancelable: true }),
    );
    expect(view.onClose).toHaveBeenCalledTimes(2);
  });

  it("searches hits, builds context, and opens the source", async () => {
    const api = installKnowledge();
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const route = makeRoute({
      mode: "library",
      job: "job-7",
      folder: "folder-9",
    });
    const view = renderSearch({ open: true, route });
    expect(
      await screen.findByRole("button", { name: "意味検索の索引を作成・更新" }),
    ).toBeEnabled();
    await user.type(screen.getByRole("searchbox", { name: "検索文" }), "budget");
    await user.selectOptions(screen.getByRole("combobox", { name: "検索方法" }), "hybrid");
    await user.selectOptions(screen.getByRole("combobox", { name: "検索対象" }), "wiki");
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "folder");

    const first = deferred();
    api.retrievalBody = task();
    api.searchReply = () => first.promise;
    const form = screen.getByRole("button", { name: "検索" }).closest("form");
    if (!(form instanceof HTMLFormElement)) throw new Error("missing form");
    await user.click(screen.getByRole("button", { name: "検索" }));
    expect(screen.getByRole("button", { name: "検索を開始…" })).toBeDisabled();
    fireEvent.submit(form);
    expect(api.searches).toHaveLength(1);
    await settle(first.resolve, json(task()));
    api.searchReply = () => json(api.retrievalBody);
    expect(await screen.findByText("該当する本文はありません。")).toBeInTheDocument();
    expect(api.searches[0]).toMatchObject({
      query: "budget",
      mode: "hybrid",
      kind: "wiki",
      source_id: null,
      folder_id: "folder-9",
    });
    expect(api.searches[0].client_request_id).toEqual(expect.any(String));

    const withHits = task({
      output: {
        results: [wikiHit, docHit],
        notice: "索引が古いです",
        stale: true,
      },
    });
    api.publish(view.query, withHits);
    expect(await screen.findByText("本文A")).toBeInTheDocument();
    expect(screen.getByText("索引が古いです")).toBeInTheDocument();
    expect(
      screen.getByText("出典が更新されました。検索をやり直してください。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("該当する本文はありません。")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Wiki · 導入/ }),
    ).toHaveTextContent("（一部抽出）");
    expect(screen.getByRole("button", { name: /Wiki · 導入/ })).toHaveTextContent("節");
    expect(screen.getByRole("button", { name: "資料 · 仕様書" })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }),
    );
    expect(api.contexts).toHaveLength(0);
    const boxes = screen.getAllByRole("checkbox", { name: "根拠に含める" });
    await user.click(boxes[0]);
    await user.click(boxes[0]);
    expect(boxes[0]).not.toBeChecked();
    await user.click(boxes[0]);
    await user.click(boxes[1]);
    api.publish(view.query, task({ output: { results: [docHit], stale: true } }));
    await waitFor(() =>
      expect(screen.getAllByRole("checkbox", { name: "根拠に含める" })).toHaveLength(1),
    );
    expect(screen.getByRole("checkbox", { name: "根拠に含める" })).toBeChecked();

    const contextGate = deferred();
    api.contextReply = () => contextGate.promise;
    await user.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    fireEvent.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    expect(api.contexts).toHaveLength(1);
    expect(api.contexts[0]).toEqual({
      retrieval_id: "s1",
      chunk_ids: ["c-doc"],
      budget: 4000,
    });
    await settle(contextGate.resolve, json({ text: "出典付き本文" }));
    expect(
      await screen.findByRole("textbox", { name: "RAGの根拠本文" }),
    ).toHaveValue("出典付き本文");
    await user.click(screen.getByRole("button", { name: "根拠本文をコピー" }));
    expect(writeText).toHaveBeenCalledWith("出典付き本文");
    writeText.mockRejectedValueOnce(new Error("コピーできません"));
    await user.click(screen.getByRole("button", { name: "根拠本文をコピー" }));
    expect(await screen.findByText("コピーできません")).toBeInTheDocument();

    const mismatched = deferred();
    api.contextReply = () => mismatched.promise;
    await user.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    api.publish(
      view.query,
      task({
        output: {
          results: [{ ...docHit, text: "更新後" }],
          notice: "索引が古いです",
        },
      }),
    );
    await settle(mismatched.resolve, json({ text: "採用されない根拠" }));
    expect(screen.queryByDisplayValue("採用されない根拠")).not.toBeInTheDocument();

    api.retrievalBody = task({
      id: "s2",
      output: { results: [wikiHit, docHit] },
    });
    await user.selectOptions(screen.getByRole("combobox", { name: "検索方法" }), "text");
    await user.selectOptions(screen.getByRole("combobox", { name: "検索対象" }), "document");
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "current");
    await user.click(screen.getByRole("button", { name: "検索" }));
    await screen.findByText("本文A");
    expect(api.searches.at(-1)).toMatchObject({
      mode: "text",
      kind: "document",
      source_id: "doc-job-7",
      folder_id: null,
    });

    api.retrievalBody = task({
      id: "s3",
      output: { results: [wikiHit, docHit] },
    });
    view.set({ route: makeRoute({ mode: "wiki", source: "ja", folder: "folder-9" }) });
    await user.selectOptions(screen.getByRole("combobox", { name: "検索対象" }), "all");
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "current");
    await user.click(screen.getByRole("button", { name: "検索" }));
    expect(api.searches.at(-1)).toMatchObject({
      kind: "all",
      source_id: "ja",
      folder_id: null,
    });
    api.retrievalBody = task({
      id: "s4",
      output: { results: [wikiHit, docHit] },
    });
    await user.selectOptions(screen.getByRole("combobox", { name: "範囲" }), "all");
    await user.click(screen.getByRole("button", { name: "検索" }));
    expect(api.searches.at(-1)).toMatchObject({ source_id: null, folder_id: null });

    view.navigate.mockClear();
    view.onClose.mockClear();
    await user.click(screen.getByRole("button", { name: /Wiki · 導入/ }));
    expect(view.onClose).toHaveBeenCalled();
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({
        mode: "wiki",
        source: "src-wiki",
        section: "intro",
        folder: "folder-9",
      }),
    );
    await user.click(screen.getByRole("button", { name: "資料 · 仕様書" }));
    expect(view.navigate).toHaveBeenCalledWith(
      makeRoute({
        mode: "library",
        source: "ja",
        job: "job-2",
        view: "preview",
        unit: 5,
        folder: "folder-9",
      }),
    );
    api.publish(view.query, task({ id: "s4", output: { results: [bareWikiHit] } }));
    await user.click(await screen.findByRole("button", { name: "Wiki · 付録" }));
    expect(view.navigate).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "wiki", source: "src-wiki", section: null }),
    );

    api.publish(
      view.query,
      task({
        id: "s4",
        error: "検索結果が失敗です",
        output: { results: [docHit], notice: "索引が古いです" },
      }),
    );
    expect(await screen.findByText("検索結果が失敗です")).toBeInTheDocument();
    expect(screen.queryByText("索引が古いです")).not.toBeInTheDocument();

    const indexGate = deferred();
    api.indexReply = () => indexGate.promise;
    api.publish(view.query, task({ id: "s4", output: { results: [docHit] } }));
    api.contextReply = () => json({ text: "コピー中の本文" });
    const contextBox = await screen.findByRole("checkbox", { name: "根拠に含める" });
    if (!(contextBox as HTMLInputElement).checked) await user.click(contextBox);
    await user.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    expect(
      await screen.findByRole("textbox", { name: "RAGの根拠本文" }),
    ).toHaveValue("コピー中の本文");
    writeText.mockClear();
    await user.click(screen.getByRole("button", { name: "意味検索の索引を作成・更新" }));
    fireEvent.click(screen.getByRole("button", { name: "根拠本文をコピー" }));
    expect(writeText).not.toHaveBeenCalled();
    await settle(indexGate.resolve, json({}));
    expect(
      await screen.findByRole("button", { name: "意味検索の索引を作成・更新" }),
    ).toBeEnabled();
  });

  it("cancels a running search and an index job", async () => {
    const api = installKnowledge();
    const user = userEvent.setup();
    api.statusBody = {
      azure_configured: true,
      configuration_error: "",
      chunks: 8,
      embedded: 2,
      cooldown_until: 1,
      jobs: [
        {
          id: "old",
          state: "failed",
          error: "前回の失敗",
          progress: 0,
          total: 1,
          output: {},
        },
        { id: "idx", state: "running", progress: 2, total: 8, output: {} },
      ],
    };
    const running = task({
      id: "s1",
      state: "running",
      progress: 0,
      total: 1,
      output: {},
    });
    api.retrievalBody = running;
    const view = renderSearch({
      open: true,
      route: makeRoute({ mode: "library", job: "job-7", folder: "folder-9" }),
    });
    expect(await screen.findByText("索引作成中 2 / 8")).toBeInTheDocument();
    expect(screen.queryByText("前回の失敗")).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "検索文" }), "budget");
    const searchGate = deferred();
    api.searchReply = () => searchGate.promise;
    await user.click(screen.getByRole("button", { name: "検索" }));
    fireEvent.click(screen.getByRole("button", { name: "索引作成を中止" }));
    fireEvent.click(screen.getByRole("button", { name: "意味検索の索引を作成・更新" }));
    expect(api.searches).toHaveLength(1);
    await settle(searchGate.resolve, json(running));
    expect(await screen.findByText("検索を処理中です。Azureの待ち時間も含みます。")).toBeInTheDocument();
    const form = screen.getByRole("searchbox", { name: "検索文" }).closest("form");
    if (!(form instanceof HTMLFormElement)) throw new Error("missing form");
    fireEvent.submit(form);
    expect(api.searches).toHaveLength(1);

    const cancelGate = deferred();
    api.cancelReply = () => cancelGate.promise;
    await user.click(screen.getByRole("button", { name: "検索を中止" }));
    fireEvent.click(screen.getByRole("button", { name: "検索を中止" }));
    const cancelled = task({
      id: "s1",
      state: "cancelled",
      error: "検索を中止しました",
      output: {},
    });
    api.retrievalBody = cancelled;
    await settle(cancelGate.resolve, json(cancelled));
    expect(await screen.findByText("検索を中止しました")).toBeInTheDocument();
    expect(
      screen.queryByText("検索を処理中です。Azureの待ち時間も含みます。"),
    ).not.toBeInTheDocument();

    const indexCancel = deferred();
    api.cancelReply = (id) => {
      expect(id).toBe("idx");
      api.statusBody = {
        azure_configured: true,
        configuration_error: "",
        chunks: 8,
        embedded: 2,
        cooldown_until: null,
        jobs: [
          {
            id: "idx",
            state: "failed",
            error: "中断しました",
            progress: 2,
            total: 8,
            output: {},
          },
        ],
      };
      return indexCancel.promise;
    };
    await user.click(screen.getByRole("button", { name: "索引作成を中止" }));
    await settle(indexCancel.resolve, json(task({ id: "idx", state: "cancelled", output: {} })));
    expect(await screen.findByText("中断しました")).toBeInTheDocument();

    api.statusBody = {
      azure_configured: true,
      configuration_error: "",
      chunks: 8,
      embedded: 8,
      cooldown_until: null,
      jobs: [],
    };
    api.indexReply = () => problem("索引を作れません");
    await user.click(screen.getByRole("button", { name: "意味検索の索引を作成・更新" }));
    expect(await screen.findByText("索引を作れません")).toBeInTheDocument();
    api.indexReply = () => json({});
    await user.click(screen.getByRole("button", { name: "意味検索の索引を作成・更新" }));
    await waitFor(() => expect(screen.queryByText("索引を作れません")).not.toBeInTheDocument());

    api.publish(view.query, task({ id: "s1", state: "failed", error: "失敗しました", output: {} }));
    expect(await screen.findByText("失敗しました")).toBeInTheDocument();
  });

  it("reports failed requests and ignores responses from an old scope", async () => {
    const api = installKnowledge();
    const user = userEvent.setup();
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    api.statusReply = () => problem("状態を取得できません", 500);
    api.retrievalReply = () =>
      json(
        task({
          id: "other",
          output: { notice: "サーバー側", results: [] },
        }),
      );
    const route = makeRoute({ mode: "library", job: "job-7", folder: "folder-9" });
    const view = renderSearch({ open: true, route });
    expect(await screen.findByText("状態を取得できません")).toBeInTheDocument();
    api.statusReply = () =>
      json({
        azure_configured: true,
        configuration_error: "",
        chunks: 1,
        embedded: 1,
        cooldown_until: null,
        jobs: [],
      });
    await act(async () => {
      await view.query.refetchQueries({ queryKey: ["knowledge-status"] });
    });
    await user.type(screen.getByRole("searchbox", { name: "検索文" }), "budget");
    api.retrievalBody = task({
      output: { notice: "クライアント側", results: [docHit] },
    });
    api.searchReply = () => json(api.retrievalBody);
    await user.click(screen.getByRole("button", { name: "検索" }));
    expect(await screen.findByText("クライアント側")).toBeInTheDocument();
    expect(screen.queryByText("サーバー側")).not.toBeInTheDocument();
    expect(screen.getByText("本文B")).toBeInTheDocument();

    api.retrievalReply = () => problem("結果を取得できません", 500);
    api.retrievalBody = task({ id: "s2", output: { results: [docHit] } });
    api.searchReply = () => json(api.retrievalBody);
    await user.click(screen.getByRole("button", { name: "検索" }));
    expect(await screen.findByText("結果を取得できません")).toBeInTheDocument();

    api.searchReply = () => problem("検索できません");
    await user.click(screen.getByRole("button", { name: "検索" }));
    expect(await screen.findByText("検索できません")).toBeInTheDocument();

    api.retrievalReply = () => json(task({ output: { results: [docHit] } }));
    api.retrievalBody = task({ output: { results: [docHit] } });
    api.searchReply = () => json(api.retrievalBody);
    await user.click(screen.getByRole("button", { name: "検索" }));
    await user.click(await screen.findByRole("checkbox", { name: "根拠に含める" }));
    api.contextReply = () => problem("根拠を作れません");
    await user.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    expect(await screen.findByText("根拠を作れません")).toBeInTheDocument();

    api.statusReply = () => json(api.statusBody);
    api.statusBody = {
      azure_configured: true,
      configuration_error: "",
      chunks: 1,
      embedded: 1,
      cooldown_until: null,
      jobs: [{ id: "idx", state: "queued", progress: 0, total: 1, output: {} }],
    };
    await act(async () => {
      await view.query.refetchQueries({ queryKey: ["knowledge-status"] });
    });
    api.cancelReply = () => problem("中止できません");
    await user.click(await screen.findByRole("button", { name: "索引作成を中止" }));
    expect(await screen.findByText("中止できません")).toBeInTheDocument();

    const searchGate = deferred();
    api.searchReply = () => searchGate.promise;
    await user.click(screen.getByRole("button", { name: "検索" }));
    view.set({ route: makeRoute({ ...route, folder: "moved" }) });
    await settle(
      searchGate.resolve,
      json(task({ output: { results: [{ ...docHit, text: "古い検索" }] } })),
    );
    expect(screen.queryByText("古い検索")).not.toBeInTheDocument();

    api.searchReply = () => json(task({ output: { results: [docHit] } }));
    api.retrievalBody = task({ output: { results: [docHit] } });
    api.retrievalReply = () => json(api.retrievalBody);
    view.set({ route });
    await user.clear(screen.getByRole("searchbox", { name: "検索文" }));
    await user.type(screen.getByRole("searchbox", { name: "検索文" }), "budget");
    await user.click(screen.getByRole("button", { name: "検索" }));
    await user.click(await screen.findByRole("checkbox", { name: "根拠に含める" }));
    const contextGate = deferred();
    api.contextReply = () => contextGate.promise;
    await user.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    view.set({ route: makeRoute({ ...route, folder: "elsewhere" }) });
    await settle(contextGate.resolve, json({ text: "古い根拠" }));
    expect(screen.queryByDisplayValue("古い根拠")).not.toBeInTheDocument();

    view.set({ route });
    await user.click(screen.getByRole("button", { name: "検索" }));
    await user.click(await screen.findByRole("checkbox", { name: "根拠に含める" }));
    api.contextReply = () => json({ text: "残る根拠" });
    await user.click(screen.getByRole("button", { name: "選んだ本文からRAGの根拠を取得" }));
    expect(
      await screen.findByRole("textbox", { name: "RAGの根拠本文" }),
    ).toHaveValue("残る根拠");
    let rejectCopy!: (reason?: unknown) => void;
    writeText.mockReturnValueOnce(
      new Promise<void>((_resolve, reject) => {
        rejectCopy = reject;
      }),
    );
    await user.click(screen.getByRole("button", { name: "根拠本文をコピー" }));
    view.set({ route: makeRoute({ ...route, job: "job-8", folder: "folder-9" }) });
    await act(async () => {
      rejectCopy(new Error("copy-stale"));
    });
    expect(screen.queryByText("copy-stale")).not.toBeInTheDocument();

    view.set({ route });
    const indexGate = deferred();
    api.indexReply = () => indexGate.promise;
    api.statusBody = {
      azure_configured: true,
      configuration_error: "",
      chunks: 1,
      embedded: 1,
      cooldown_until: null,
      jobs: [],
    };
    await act(async () => {
      await view.query.refetchQueries({ queryKey: ["knowledge-status"] });
    });
    await user.click(
      await screen.findByRole("button", { name: "意味検索の索引を作成・更新" }),
    );
    view.set({ route: makeRoute({ ...route, source: "left" }) });
    await settle(indexGate.resolve, problem("index-stale"));
    expect(screen.queryByText("index-stale")).not.toBeInTheDocument();
  });
});
