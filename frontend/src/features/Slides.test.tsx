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
import { afterEach, expect, test, vi } from "vitest";
import { useSlides } from "./Slides";
import type { Job, Language } from "../lib/types";

const originalScroll = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "scrollIntoView",
);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (originalScroll)
    Object.defineProperty(
      HTMLElement.prototype,
      "scrollIntoView",
      originalScroll,
    );
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
});

test("restarts unfinished thumbnail requests when metadata replaces the observer", async () => {
  const observers: { targets: Element[]; emit: () => void }[] = [];
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      targets: Element[] = [];
      constructor(private receive: IntersectionObserverCallback) {
        observers.push(this);
      }
      observe(target: Element) {
        this.targets.push(target);
      }
      unobserve() {}
      disconnect() {}
      emit() {
        this.receive(
          this.targets.map(
            (target) =>
              ({ target, isIntersecting: true }) as IntersectionObserverEntry,
          ),
          this as unknown as IntersectionObserver,
        );
      }
    },
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(100);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(100);
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  const job: Job = {
    id: "job",
    filename: "file.pptx",
    folder_id: null,
    state: "success",
    created: 100,
    duration: null,
    pages: 1,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: true,
  };
  const query = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity } },
  });
  const metadata = {
    slides: [{ number: 1, width: 500, height: 300, preview: "slide.html" }],
  };
  const key = ["slides", job.id, job.preview];
  query.setQueryData(key, metadata);
  function Preview() {
    return useSlides(job, true, 1, () => {}, "original", "").content;
  }
  const view = render(
    <QueryClientProvider client={query}>
      <Preview />
    </QueryClientProvider>,
  );
  const image = view.container.querySelector("img")!;
  act(() => observers.at(-1)!.emit());
  expect(image).toHaveAttribute("src", "/api/jobs/job/slides/1/thumbnail");
  act(() =>
    query.setQueryData(key, {
      slides: [{ ...metadata.slides[0], width: 600 }],
    }),
  );
  await waitFor(() => expect(observers.length).toBe(2));
  act(() => observers.at(-1)!.emit());
  expect(image).toHaveAttribute("src", "/api/jobs/job/slides/1/thumbnail");
});

function slideJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job",
    filename: "file.pptx",
    folder_id: null,
    state: "success",
    created: 100,
    duration: null,
    pages: 3,
    tables: 0,
    pictures: 0,
    chunks: 0,
    search_chunks: 0,
    rag_policy: null,
    error: null,
    preview: "preview.html",
    slide_layout: true,
    ...overrides,
  };
}
function slides(count: number, preview: string | null = "slide.html") {
  return Array.from({ length: count }, (_, index) => ({
    number: index + 1,
    width: 400,
    height: 200,
    preview,
  }));
}
function installSlides(box = { width: 200, height: 160 }) {
  const observers: {
    emit: (entries?: Partial<IntersectionObserverEntry>[]) => void;
  }[] = [];
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(private receive: IntersectionObserverCallback) {
        observers.push({
          emit: (entries) => {
            const images = Array.from(document.querySelectorAll("img"));
            this.receive(
              (entries ??
                images.map((target) => ({
                  target,
                  isIntersecting: true,
                }))) as IntersectionObserverEntry[],
              this as unknown as IntersectionObserver,
            );
          },
        });
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
    () => box.width,
  );
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(
    () => box.height,
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  return { observers, box };
}
function renderSlides(
  data: ReturnType<typeof slides>,
  props: {
    visible?: boolean;
    initial?: number;
    language?: Language;
    revision?: string;
    job?: Job;
  } = {},
) {
  const current = props.job ?? slideJob();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  if (data.length)
    query.setQueryData(["slides", current.id, current.preview], {
      slides: data,
    });
  function Harness({
    visible = true,
    language = "original" as Language,
    revision = "",
  }: {
    visible?: boolean;
    language?: Language;
    revision?: string;
  }) {
    const [number, setNumber] = useState(props.initial ?? data[0]?.number ?? 1);
    const view = useSlides(
      current,
      visible,
      number,
      setNumber,
      language,
      revision,
    );
    return (
      <>
        <output data-testid="slide-meta">
          {`${view.active}|${view.fallback}|${view.selectionRevision}|${view.url}`}
        </output>
        {view.controls}
        {view.content}
      </>
    );
  }
  const view = render(
    <QueryClientProvider client={query}>
      <Harness
        visible={props.visible}
        language={props.language}
        revision={props.revision}
      />
    </QueryClientProvider>,
  );
  return {
    ...view,
    query,
    rerenderHarness(
      next: { visible?: boolean; language?: Language; revision?: string } = {},
    ) {
      view.rerender(
        <QueryClientProvider client={query}>
          <Harness
            visible={next.visible ?? props.visible}
            language={next.language ?? props.language}
            revision={next.revision ?? props.revision}
          />
        </QueryClientProvider>,
      );
    },
  };
}

test("navigates, zooms, and scales a slide deck", () => {
  const box = { width: 0, height: 0 };
  installSlides(box);
  const view = renderSlides(slides(3));
  const stage = screen.getByRole("region", { name: "スライド表示領域" });
  box.width = 200;
  box.height = 160;
  stage.scrollTop = 40;
  act(() => window.dispatchEvent(new Event("resize")));
  expect(stage.scrollTop).toBe(0);
  act(() => window.dispatchEvent(new Event("resize")));

  fireEvent.click(
    screen.getByRole("button", { name: "サムネイルを表示・非表示" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "サムネイルを表示・非表示" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "前のスライド" }));
  fireEvent.click(screen.getByRole("button", { name: "次のスライド" }));
  expect(screen.getByLabelText("スライド")).toHaveValue("2");
  fireEvent.change(screen.getByLabelText("スライド"), {
    target: { value: "3" },
  });
  expect(screen.getByLabelText("スライド")).toHaveValue("3");
  fireEvent.click(screen.getByRole("button", { name: "次のスライド" }));
  fireEvent.click(screen.getByRole("button", { name: "スライド 1" }));
  expect(screen.getByLabelText("スライド")).toHaveValue("1");

  const current = screen.getByRole("button", { name: "スライド 1" });
  fireEvent.keyDown(current, { key: "ArrowUp" });
  fireEvent.keyDown(current, { key: "Home" });
  fireEvent.keyDown(screen.getByRole("button", { name: "スライド 1" }), {
    key: "End",
  });
  fireEvent.keyDown(screen.getByRole("button", { name: "スライド 3" }), {
    key: "ArrowDown",
  });
  fireEvent.keyDown(screen.getByRole("button", { name: "スライド 3" }), {
    key: "ArrowUp",
  });
  fireEvent.keyDown(screen.getByRole("button", { name: "スライド 2" }), {
    key: "Enter",
  });
  fireEvent.click(screen.getByRole("button", { name: "スライド 1" }));

  fireEvent.change(screen.getByLabelText("ズーム倍率"), {
    target: { value: "current" },
  });
  fireEvent.change(screen.getByLabelText("ズーム倍率"), {
    target: { value: "2" },
  });
  const zoomIn = screen.getByRole("button", { name: "拡大" });
  for (let step = 0; step < 5; step++) fireEvent.click(zoomIn);
  expect(zoomIn).toBeDisabled();
  fireEvent.change(screen.getByLabelText("ズーム倍率"), {
    target: { value: "0.25" },
  });
  const zoomOut = screen.getByRole("button", { name: "縮小" });
  for (let step = 0; step < 6; step++) fireEvent.click(zoomOut);
  expect(zoomOut).toBeDisabled();
  stage.scrollTop = 30;
  stage.scrollLeft = 12;
  act(() => window.dispatchEvent(new Event("resize")));
  expect(stage.scrollTop).toBe(30);
  fireEvent.click(screen.getByRole("button", { name: "画面に合わせる" }));
  fireEvent.keyDown(document.getElementById("slideSurface")!, {
    key: "ArrowRight",
  });
  expect(screen.getByLabelText("スライド")).toHaveValue("1");
  fireEvent.keyDown(stage, { key: "a" });
  fireEvent.keyDown(stage, { key: "ArrowRight" });
  expect(screen.getByLabelText("スライド")).toHaveValue("2");
  fireEvent.keyDown(stage, { key: "ArrowLeft" });
  expect(screen.getByLabelText("スライド")).toHaveValue("1");
  fireEvent.keyDown(stage, { key: "ArrowLeft" });
  expect(screen.getByTestId("slide-meta")).toHaveTextContent(/^true\|false\|/);
  view.rerenderHarness({ visible: false });
  expect(screen.getByLabelText("スライド")).not.toBeVisible();
});

test("keeps a 530-page recovery preview to one passive frame with page and zoom controls", async () => {
  const { observers } = installSlides();
  const data = slides(530).map((s) => ({
    ...s,
    preview: `progressive-preview/page-${s.number}.html`,
  }));
  const view = renderSlides(data, {
    job: slideJob({
      pages: 530,
      slide_layout: false,
      preview: "progressive-preview/revision-530.html",
    }),
  });
  const frame = () => view.container.querySelector("iframe")!;
  await waitFor(() => expect(frame()).not.toBeNull());
  expect(view.container.querySelectorAll("iframe")).toHaveLength(1);
  expect(view.container.querySelectorAll("img")).toHaveLength(530);
  expect(view.container.querySelector("img")).not.toHaveAttribute("src");
  expect(frame()).toHaveAttribute(
    "src",
    "/files/job/progressive-preview/page-1.html?inline_fonts=true",
  );
  expect(frame()).toHaveAttribute("sandbox", "");
  const images = [...view.container.querySelectorAll("img")];
  act(() =>
    observers
      .at(-1)!
      .emit(
        images.slice(0, 8).map((target) => ({ target, isIntersecting: true })),
      ),
  );
  expect(images.filter((img) => img.hasAttribute("src"))).toHaveLength(2);
  expect(images[0]).toHaveAttribute(
    "src",
    "/files/job/progressive-preview/page-1.html?thumbnail=true",
  );
  fireEvent.load(images[0]);
  expect(images.filter((img) => img.hasAttribute("src"))).toHaveLength(3);

  expect(
    screen.getByRole("button", { name: "サムネイルを表示・非表示" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "次のスライド" }));
  expect(frame()).toHaveAttribute(
    "src",
    "/files/job/progressive-preview/page-2.html?inline_fonts=true",
  );
  fireEvent.change(screen.getByLabelText("スライド"), {
    target: { value: "530" },
  });
  expect(frame()).toHaveAttribute(
    "src",
    "/files/job/progressive-preview/page-530.html?inline_fonts=true",
  );
  expect(screen.getByRole("button", { name: "次のスライド" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "拡大" }));
  expect(
    screen.getByRole("button", { name: "画面に合わせる" }),
  ).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(screen.getByRole("button", { name: "画面に合わせる" }));
  expect(
    screen.getByRole("button", { name: "画面に合わせる" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(view.container.querySelectorAll("iframe")).toHaveLength(1);
});

test("drops offscreen thumbnails from the queue and loads them when they return", () => {
  const { observers } = installSlides();
  const view = renderSlides(slides(8));
  const images = [...view.container.querySelectorAll("img")];
  const observer = observers.at(-1)!;
  act(() => observer.emit());
  expect(images.filter((image) => image.hasAttribute("src"))).toHaveLength(2);
  act(() =>
    observer.emit([
      ...images
        .slice(2, 6)
        .map((target) => ({ target, isIntersecting: false })),
    ]),
  );
  fireEvent.load(images[0]);
  expect(images[2]).not.toHaveAttribute("src");
  expect(images[6]).toHaveAttribute("src");
  act(() => observer.emit([{ target: images[2], isIntersecting: true }]));
  fireEvent.load(images[1]);
  fireEvent.load(images[6]);
  expect(images[2]).toHaveAttribute("src");
});

test("scrolls to the initially requested page after asynchronous metadata arrives", async () => {
  installSlides();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ slides: slides(530) })),
  );
  const view = renderSlides([], {
    initial: 530,
    job: slideJob({ pages: 530 }),
  });
  await waitFor(() =>
    expect(view.container.querySelectorAll("img")).toHaveLength(530),
  );
  const scroller = vi.mocked(HTMLElement.prototype.scrollIntoView);
  expect(scroller.mock.contexts).toContain(
    view.container.querySelector('[data-page="530"]'),
  );
});

test("retains manual pan when metadata gains another saved page", async () => {
  installSlides();
  const view = renderSlides(slides(3));
  fireEvent.change(screen.getByLabelText("ズーム倍率"), {
    target: { value: "2" },
  });
  const stage = document.getElementById("slideStage")!;
  stage.scrollLeft = 40;
  stage.scrollTop = 30;
  act(() =>
    view.query.setQueryData(["slides", "job", "preview.html"], {
      slides: slides(4),
    }),
  );
  await waitFor(() =>
    expect(view.container.querySelectorAll("img")).toHaveLength(4),
  );
  expect(stage.scrollLeft).toBe(40);
  expect(stage.scrollTop).toBe(30);
});

test("keeps the new saved-thumbnail URL when switching modes with requests in flight", () => {
  const { observers } = installSlides();
  const job = slideJob();
  const view = renderSlides(slides(3), { job });
  act(() => observers.at(-1)!.emit());
  Object.assign(job, {
    preview: "progressive-preview/revision-3.html",
    slide_layout: false,
  });
  act(() => {
    view.query.setQueryData(["slides", job.id, job.preview], {
      slides: slides(3).map((slide) => ({
        ...slide,
        preview: `progressive-preview/page-${slide.number}.html`,
      })),
    });
    view.rerenderHarness();
  });
  act(() => observers.at(-1)!.emit());
  expect(view.container.querySelector("img")).toHaveAttribute(
    "src",
    "/files/job/progressive-preview/page-1.html?thumbnail=true",
  );
});

test("ignores unfit messages and follows slide keys from the frame", () => {
  installSlides();
  renderSlides(slides(2));
  const source = window;
  const send = (
    data: unknown,
    frameSource: MessageEventSource | null = source,
  ) => {
    const frame = screen.getByTitle(/原本プレビュー/);
    Object.defineProperty(frame, "contentWindow", {
      configurable: true,
      value: source,
    });
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data, source: frameSource }),
      );
    });
  };
  fireEvent.click(screen.getByRole("button", { name: "拡大" }));
  send({ jobId: "job", type: "docling-slide-key", key: "ArrowRight" });
  expect(screen.getByLabelText("スライド")).toHaveValue("1");
  fireEvent.click(screen.getByRole("button", { name: "画面に合わせる" }));
  send(null);
  send(["ArrowRight"]);
  send({ jobId: "other", type: "docling-slide-key", key: "ArrowRight" });
  send({ jobId: "job", type: "other", key: "ArrowRight" });
  send({ jobId: "job", type: "docling-slide-key", key: "Home" });
  send({ jobId: "job", type: "docling-slide-key", key: "ArrowLeft" });
  expect(screen.getByLabelText("スライド")).toHaveValue("1");
  send(
    { jobId: "job", type: "docling-slide-key", key: "ArrowRight" },
    {} as MessageEventSource,
  );
  send({ jobId: "job", type: "docling-slide-key", key: "ArrowRight" });
  expect(screen.getByLabelText("スライド")).toHaveValue("2");
  send({ jobId: "job", type: "docling-slide-key", key: "ArrowRight" });
  expect(screen.getByLabelText("スライド")).toHaveValue("2");
  send({ jobId: "job", type: "docling-slide-key", key: "ArrowLeft" });
  expect(screen.getByLabelText("スライド")).toHaveValue("1");
});

test("refreshes a translated slide in place and skips original revisions", () => {
  installSlides();
  const job = slideJob();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  query.setQueryData(["slides", job.id, job.preview], { slides: slides(2) });
  function View({
    language,
    revision,
    number = 1,
  }: {
    language: Language;
    revision: string;
    number?: number;
  }) {
    const view = useSlides(job, true, number, () => {}, language, revision);
    return (
      <>
        {view.controls}
        {view.content}
      </>
    );
  }
  const view = render(
    <QueryClientProvider client={query}>
      <View language="en" revision="" />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "拡大" }));
  const stage = screen.getByRole("region", { name: "スライド表示領域" });
  const frame = screen.getByTitle("原本プレビュー・スライド 1");
  stage.scrollTop = 0;
  stage.scrollLeft = 0;
  view.rerender(
    <QueryClientProvider client={query}>
      <View language="en" revision="a" />
    </QueryClientProvider>,
  );
  stage.scrollTop = 25;
  stage.scrollLeft = 8;
  view.rerender(
    <QueryClientProvider client={query}>
      <View language="en" revision="b" />
    </QueryClientProvider>,
  );
  fireEvent.load(frame);
  expect(stage.scrollTop).toBe(25);
  expect(stage.scrollLeft).toBe(8);
  view.rerender(
    <QueryClientProvider client={query}>
      <View language="original" revision="c" />
    </QueryClientProvider>,
  );
  view.rerender(
    <QueryClientProvider client={query}>
      <View language="original" revision="d" />
    </QueryClientProvider>,
  );
  view.rerender(
    <QueryClientProvider client={query}>
      <View language="original" revision="d" number={2} />
    </QueryClientProvider>,
  );
  expect(screen.getByTitle("原本プレビュー・スライド 2")).toBeInTheDocument();
});

test("explains missing slide layouts and a hidden deck", async () => {
  installSlides();
  const pending = renderSlides([], { job: slideJob({ id: "pending" }) });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => undefined)),
  );
  pending.rerenderHarness();
  expect(screen.getByText("スライドを読み込んでいます…")).toBeInTheDocument();

  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ detail: "slide missing" }, { status: 500 }),
    ),
  );
  const failed = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Failed() {
    const view = useSlides(
      slideJob({ id: "bad" }),
      true,
      1,
      () => {},
      "original",
      "",
    );
    return (
      <>
        <output data-testid="slide-meta">{`${view.active}|${view.fallback}`}</output>
        {view.content}
      </>
    );
  }
  render(
    <QueryClientProvider client={failed}>
      <Failed />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("slide missing")).toBeInTheDocument();
  expect(screen.getAllByTestId("slide-meta").at(-1)).toHaveTextContent(
    "false|true",
  );

  renderSlides([{ number: 1, width: 320, height: 180, preview: null }]);
  expect(
    screen.getByText(
      "スライド別表示を取得できないため、文書全体のプレビューを表示します。",
    ),
  ).toBeInTheDocument();

  const plain = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  function Plain() {
    const view = useSlides(
      slideJob({ slide_layout: false, id: "plain" }),
      true,
      1,
      () => {},
      "original",
      "",
    );
    return view.content;
  }
  render(
    <QueryClientProvider client={plain}>
      <Plain />
    </QueryClientProvider>,
  );
  expect(fetchMock).not.toHaveBeenCalled();
});

test("keeps an unknown slide number inert until a neighbor exists", () => {
  installSlides();
  renderSlides(slides(2), { initial: 9, visible: false });
  expect(screen.queryByTitle(/原本プレビュー/)).toBeNull();
});

test("loads visible thumbnails and records failures", () => {
  const { observers } = installSlides();
  const view = renderSlides(slides(3));
  const images = () => Array.from(document.querySelectorAll("img"));
  act(() => observers.at(-1)?.emit());
  expect(images().filter((img) => img.getAttribute("src")).length).toBe(2);
  const [first, second] = images();
  first.dispatchEvent(new Event("load"));
  expect(first.parentElement).not.toHaveClass("loading");
  expect(images().filter((img) => img.getAttribute("src")).length).toBe(3);
  second.dispatchEvent(new Event("error"));
  expect(second.parentElement).toHaveClass("failed");
  const pending = images()[2];
  expect(pending?.getAttribute("src")).toContain("/thumbnail");
  view.unmount();
  expect(pending?.dataset.url).toContain("/thumbnail");
  expect(pending?.getAttribute("src")).toBeNull();
  pending?.dispatchEvent(new Event("load"));
});

test("does not request thumbnails that are disconnected or already queued", () => {
  const box = { width: 0, height: 0 };
  const { observers } = installSlides(box);
  renderSlides(slides(2));
  const images = () => Array.from(document.querySelectorAll("img"));
  act(() => observers.at(-1)?.emit());
  act(() => observers.at(-1)?.emit());
  expect(images().every((img) => !img.getAttribute("src"))).toBe(true);
  images()[0]?.remove();
  box.width = 200;
  box.height = 160;
  const stray = document.createElement("img");
  act(() =>
    observers.at(-1)?.emit([
      { target: document.createElement("div"), isIntersecting: true },
      { target: stray, isIntersecting: true },
      { target: images()[0], isIntersecting: false },
    ]),
  );
  expect(images().every((img) => !img.getAttribute("src"))).toBe(true);
  act(() => observers.at(-1)?.emit());
  expect(images().some((img) => img.getAttribute("src"))).toBe(true);
});

test("drops revision refreshes when the slide frame is absent", () => {
  installSlides();
  const job = slideJob();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  query.setQueryData(["slides", job.id, job.preview], {
    slides: [{ number: 1, width: 400, height: 200, preview: null }],
  });
  function View({ revision }: { revision: string }) {
    return useSlides(job, true, 1, () => {}, "original", revision).content;
  }
  const view = render(
    <QueryClientProvider client={query}>
      <View revision="" />
    </QueryClientProvider>,
  );
  view.rerender(
    <QueryClientProvider client={query}>
      <View revision="next" />
    </QueryClientProvider>,
  );
  expect(screen.queryByTitle(/原本プレビュー/)).toBeNull();
});
