import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  passiveDocument,
  printUnits,
  renderPrintDocument,
  selectPrintUnits,
  waitForPrintAssets,
  type PrintSettings,
  type PrintUnit,
} from "./print-document";
import type { Language } from "../lib/types";

const units = [1, 2, 3, 4, 5].map((number) => ({
  number,
  url: `/pages/${number}`,
}));
const signal = () => new AbortController().signal;

beforeAll(() => {
  HTMLImageElement.prototype.decode = function () {
    return Promise.resolve();
  };
  Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", {
    configurable: true,
    get() {
      return 8;
    },
  });
  Object.defineProperty(Document.prototype, "fonts", {
    configurable: true,
    get() {
      return { ready: Promise.resolve() };
    },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.title = "";
  document.body.replaceChildren();
  document.head.querySelectorAll("style").forEach((node) => node.remove());
});

function textResponse(body: string, status = 200) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
function installFetch(
  handler: (url: URL, init?: RequestInit) => Response | Promise<Response>,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? new URL(input, location.origin)
          : input instanceof URL
            ? input
            : new URL(input.url);
      return handler(url, init);
    }),
  );
}
function sourceJob(
  filename: string,
  extra: {
    preview?: string | null;
    slide_layout?: boolean;
    pages?: number;
    original_filename?: string | null;
    id?: string;
  } = {},
) {
  return {
    id: extra.id ?? "job",
    filename,
    original_filename:
      extra.original_filename === undefined ? null : extra.original_filename,
    preview: extra.preview === undefined ? "preview.html" : extra.preview,
    slide_layout: extra.slide_layout ?? false,
    pages: extra.pages ?? 1,
  };
}

describe("print document", () => {
  it("selects current and custom ranges in document order without duplicates", () => {
    expect(selectPrintUnits(units, "all", 3, "9").map((u) => u.number)).toEqual([
      1, 2, 3, 4, 5,
    ]);
    expect(
      selectPrintUnits(units, "current", 3, "").map((u) => u.number),
    ).toEqual([3]);
    expect(selectPrintUnits(units, "current", 9, "").map((u) => u.number)).toEqual(
      [],
    );
    expect(
      selectPrintUnits(units, "range", 1, "5, 1-3, 2").map((u) => u.number),
    ).toEqual([1, 2, 3, 5]);
    expect(
      selectPrintUnits(units, "range", 1, " 1、3 - 4 , 2 ").map((u) => u.number),
    ).toEqual([1, 2, 3, 4]);
    for (const range of ["", "0", "6", "3-1", "1-9999999", "1,"])
      expect(() => selectPrintUnits(units, "range", 1, range)).toThrow();
  });
  it("keeps text, SVG and local assets while stripping active content and navigation", () => {
    const base = new URL("/view/job/pages/1", location.origin).href;
    const doc = passiveDocument(
      `<html><head><script>bad()</script><base href="https://evil.example"><meta http-equiv="refresh" content="0"></head><body onload="bad()"><p>日本語</p><svg><defs><path id="shape"/></defs><use href="#shape"/><text>文字</text></svg><img src="/files/job/picture.png" onerror="bad()"><img src="https://evil.example/p.png"><a href="javascript:bad()">link</a><iframe srcdoc="bad"></iframe><form>bad</form></body></html>`,
      base,
    );
    expect(doc.body.textContent).toContain("日本語");
    expect(doc.querySelector("svg text")?.textContent).toBe("文字");
    expect(doc.querySelector("use")?.getAttribute("href")).toBe("#shape");
    expect(doc.querySelector("img")?.getAttribute("src")).toBe(
      new URL("/files/job/picture.png", location.origin).href,
    );
    expect(doc.querySelectorAll("img")[1].hasAttribute("src")).toBe(false);
    expect(
      doc.querySelector(
        "script,iframe,form,base,meta[http-equiv],[onload],[onerror],a[href]",
      ),
    ).toBeNull();
  });
  it("resolves inline background images and tolerates invalid URLs", () => {
    const base = new URL("/files/job/quicklook/Preview.html", location.origin)
      .href;
    const doc = passiveDocument(
      '<p style="background-image:URL(picture.png)">text</p><img src="http://[invalid"><img style="background:url(https://evil.example/image.png)">',
      base,
    );
    expect(doc.querySelector("p")?.style.backgroundImage).toContain(
      new URL("picture.png", base).href,
    );
    expect(doc.querySelector("img")?.hasAttribute("src")).toBe(false);
    expect(doc.body.innerHTML).not.toContain("evil.example");
  });
  it("drops non-image data urls while keeping image data and visible text", () => {
    const base = new URL("/view/job/pages/1", location.origin).href;
    const doc = passiveDocument(
      '<p>本文</p><img src="data:text/html,<script>bad()</script>"><img src="data:image/png;base64,aaaa"><svg><image href="data:text/html,bad"></image></svg>',
      base,
    );
    expect(doc.body.textContent).toContain("本文");
    const images = [...doc.querySelectorAll("img")];
    expect(images[0]?.hasAttribute("src")).toBe(false);
    expect(images[1]?.getAttribute("src")).toBe("data:image/png;base64,aaaa");
    expect(doc.querySelector("image")?.hasAttribute("href")).toBe(false);
  });
  it("cancels pending font and image decoding without waiting indefinitely", async () => {
    for (const pendingFonts of [true, false]) {
      const doc = document.implementation.createHTMLDocument();
      Object.defineProperty(doc, "fonts", {
        value: {
          ready: pendingFonts ? new Promise(() => {}) : Promise.resolve(),
        },
      });
      const img = doc.createElement("img");
      img.src = "/files/job/picture.png";
      img.decode = vi.fn(() => new Promise<void>(() => {}));
      const controller = new AbortController();
      const loading = waitForPrintAssets(doc, [img], controller.signal);
      controller.abort(new Error("cancelled"));
      await expect(loading).rejects.toThrow("cancelled");
    }
  });
  it("reports undecodable images as a loading failure", async () => {
    const doc = document.implementation.createHTMLDocument();
    Object.defineProperty(doc, "fonts", {
      value: { ready: Promise.resolve() },
    });
    const img = doc.createElement("img");
    img.src = "/files/job/broken.png";
    img.decode = vi.fn(() => Promise.reject(new Error("decode failed")));
    await expect(
      waitForPrintAssets(doc, [img], new AbortController().signal),
    ).rejects.toThrow("印刷用の画像を読み込めませんでした");
  });
  it("strips active markup and rewrites only safe asset urls", () => {
    const base = new URL("/view/job/pages/1", location.origin).href;
    const doc = passiveDocument(
      `<html><head><link rel="stylesheet" href="/files/job/ok.css"><link rel="stylesheet" href="https://evil.example/x.css"><object></object><embed src="/x"></head><body><img src="data:font/woff;base64,qq" id="font"><img src="data:application/font-woff;base64,qq" id="appfont"><img src="data:application/octet-stream;base64,qq" id="bin"><img src="   /files/a.png   " id="trim"><img src="" id="empty"><img src="http://[" id="bad"><a href="/files/a.png" target="_blank" download="f">label</a><button formaction="/go" srcset="a.png" onclick="bad()">x</button><div action="/go"></div><svg><image href="#sym"></image><image xlink:href="https://evil.example/a.png"></image><image href="/files/c.png"></image></svg><div id="styled" style="background:url();color:red;border-image:url('ok.png');mask-image:url(https://evil.example/e.png)"></div></body></html>`,
      base,
    );
    expect(doc.querySelector("#font")?.getAttribute("src")).toBe(
      "data:font/woff;base64,qq",
    );
    expect(doc.querySelector("#appfont")?.getAttribute("src")).toBe(
      "data:application/font-woff;base64,qq",
    );
    expect(doc.querySelector("#bin")?.hasAttribute("src")).toBe(false);
    expect(doc.querySelector("#trim")?.getAttribute("src")).toBe(
      new URL("/files/a.png", location.origin).href,
    );
    expect(doc.querySelector("#empty")?.hasAttribute("src")).toBe(false);
    expect(doc.querySelector("#bad")?.hasAttribute("src")).toBe(false);
    expect(doc.querySelector("img")?.getAttribute("loading")).toBe("eager");
    expect(doc.querySelector("a")?.textContent).toBe("label");
    expect(doc.querySelector("a")?.hasAttribute("href")).toBe(false);
    expect(doc.querySelector("a")?.hasAttribute("target")).toBe(false);
    expect(doc.querySelector("a")?.hasAttribute("download")).toBe(false);
    expect(doc.querySelector("button")?.hasAttribute("formaction")).toBe(false);
    expect(doc.querySelector("button")?.hasAttribute("srcset")).toBe(false);
    expect(doc.querySelector("button")?.hasAttribute("onclick")).toBe(false);
    expect(doc.querySelector("div")?.hasAttribute("action")).toBe(false);
    expect(doc.querySelector("object,embed,link[href*='evil']")).toBeNull();
    expect(doc.querySelector("link")?.getAttribute("href")).toBe(
      new URL("/files/job/ok.css", location.origin).href,
    );
    const images = [...doc.querySelectorAll("image")];
    expect(images[0]?.getAttribute("href")).toBe("#sym");
    expect(images[1]?.hasAttribute("xlink:href")).toBe(false);
    expect(images[2]?.getAttribute("href")).toBe(
      new URL("/files/c.png", location.origin).href,
    );
    const style = doc.querySelector("#styled")?.getAttribute("style") || "";
    expect(style).toContain(new URL("ok.png", base).href);
    expect(style).toContain("none");
    expect(style).not.toContain("evil.example");
  });
  it("resolves when fonts and images are ready and rejects broken or cancelled assets", async () => {
    const doc = document.implementation.createHTMLDocument();
    const blank = doc.createElement("img");
    const ready = doc.createElement("img");
    ready.src = "/files/job/picture.png";
    await expect(
      waitForPrintAssets(doc, [blank, ready], signal()),
    ).resolves.toBeUndefined();
    const zero = doc.createElement("img");
    zero.src = "/files/job/zero.png";
    Object.defineProperty(zero, "naturalWidth", { configurable: true, value: 0 });
    await expect(waitForPrintAssets(doc, [zero], signal())).rejects.toThrow(
      "印刷用の画像を読み込めませんでした",
    );
    const failedFonts = document.implementation.createHTMLDocument();
    const fontError = Promise.reject(new Error("font failed"));
    fontError.catch(() => {});
    Object.defineProperty(failedFonts, "fonts", {
      configurable: true,
      value: { ready: fontError },
    });
    await expect(waitForPrintAssets(failedFonts, [], signal())).rejects.toThrow(
      "font failed",
    );
    const aborted = new AbortController();
    aborted.abort(new Error("already"));
    await expect(waitForPrintAssets(doc, [], aborted.signal)).rejects.toThrow(
      "already",
    );
  });
  it("builds print units for each supported preview shape", async () => {
    await expect(
      printUnits(sourceJob("a.pdf", { preview: null, pages: 4 }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
    await expect(
      printUnits(sourceJob("a.pdf", { preview: "", id: "a/b" }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/a%2Fb/extracted", extracted: true },
    ]);
    await expect(
      printUnits(
        sourceJob("ignored.pdf", { original_filename: "Report.DOCX" }),
        signal(),
      ),
    ).resolves.toEqual([{ number: 1, url: "/view/job/word" }]);
    for (const name of ["notes.md", "notes.markdown", "notes.txt", "notes.text"])
      await expect(printUnits(sourceJob(name), signal())).resolves.toEqual([
        { number: 1, url: "/view/job/document" },
      ]);
    await expect(
      printUnits(sourceJob("deck.pptx", { original_filename: "" }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
    const slides = {
      slides: [
        { number: 2, width: 800, height: 450, preview: "two.html" },
        { number: 3, width: 640, height: 360, preview: "three.html" },
      ],
    };
    installFetch((url) => {
      expect(url.pathname).toBe("/files/job/slides.json");
      return jsonResponse(slides);
    });
    await expect(
      printUnits(sourceJob("deck.pptx", { slide_layout: true }), signal()),
    ).resolves.toEqual([
      { number: 2, url: "/view/job/slides/2", width: 800, height: 450 },
      { number: 3, url: "/view/job/slides/3", width: 640, height: 360 },
    ]);
    installFetch(() =>
      jsonResponse({
        slides: [{ number: 1, width: 10, height: 10, preview: null }],
      }),
    );
    await expect(
      printUnits(sourceJob("deck.pptx", { slide_layout: true }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
    installFetch(() => jsonResponse({ slides: [] }));
    await expect(
      printUnits(sourceJob("deck.pptx", { slide_layout: true }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
    installFetch(() =>
      jsonResponse({
        slides: [
          { number: 1, width: 10, height: 10, preview: "ok.html" },
          { number: 2, width: 10, height: 10, preview: "" },
        ],
      }),
    );
    await expect(
      printUnits(sourceJob("deck.pptx", { slide_layout: true }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
    installFetch(() => textResponse("<html><body>no pages</body></html>"));
    await expect(printUnits(sourceJob("a.pdf"), signal())).rejects.toThrow(
      "PDFのページ情報を読み込めませんでした。",
    );
    const invalid = [
      `<div class="pdf-page" data-number="1" data-width="0" data-height="10"></div>`,
      `<div class="pdf-page" data-number="1" data-width="10" data-height="0"></div>`,
      `<div class="pdf-page" data-number="1" data-width="nope" data-height="10"></div>`,
      `<div class="pdf-page" data-number="1" data-width="10" data-height="-5"></div>`,
      `<div class="pdf-page" data-number="1" data-width="Infinity" data-height="10"></div>`,
    ];
    for (const page of invalid) {
      installFetch(() => textResponse(page));
      await expect(printUnits(sourceJob("a.pdf"), signal())).rejects.toThrow(
        "PDFのページ情報を読み込めませんでした。",
      );
    }
    installFetch(() => textResponse("missing", 502));
    await expect(printUnits(sourceJob("a.pdf"), signal())).rejects.toThrow(
      "印刷用の文書を読み込めませんでした（HTTP 502）",
    );
    installFetch(() =>
      textResponse(
        `<div class="pdf-page" data-number="4" data-width="600" data-height="800"></div><div class="pdf-page" data-number="5" data-width="100.5" data-height="200"></div>`,
      ),
    );
    await expect(printUnits(sourceJob("Scan.PDF"), signal())).resolves.toEqual([
      { number: 4, url: "/view/job/pages/4", width: 600, height: 800 },
      { number: 5, url: "/view/job/pages/5", width: 100.5, height: 200 },
    ]);
    await expect(
      printUnits(sourceJob("book.xlsx", { pages: 2 }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/sheets/1" },
      { number: 2, url: "/view/job/sheets/2" },
    ]);
    await expect(
      printUnits(sourceJob("book.xlsx", { pages: 0 }), signal()),
    ).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
    await expect(printUnits(sourceJob("data.csv"), signal())).resolves.toEqual([
      { number: 1, url: "/view/job/extracted", extracted: true },
    ]);
  });
  it("renders fixed, flowing, quirks, and extracted pages and cancels in-flight loads", async () => {
    const rich = `<!DOCTYPE html><html><head><link rel="stylesheet" href="/files/job/extra.css"><link rel="stylesheet"><style>@font-face { font-family: One; src: url("/files/job/one.woff"); }@font-face { font-family: One; src: url("/files/job/one.woff"); }@font-face { font-family: Two; src: url("data:font/woff2,qq"); }@page { margin: 0; }html, :root, body { color: black; }@media screen { body { margin: 1px; } }@supports (display: block) { p { color: blue; } }@keyframes spin { from { opacity: 0; } to { opacity: 1; } }.somebody { color: red; }</style></head><body><p class="asset-probe">hello</p><img src="/files/job/photo.png"><img><svg><image href="/files/job/vector.png"></image><image href="#sym"></image><image xlink:href="/files/job/linked.png"></image></svg></body></html>`;
    const quirks = `<html><head><style>p { width: 12; min-height: 4; max-width: 20; font-size: 10; text-indent: 1; top: -2; left: 0; right: 1; bottom: 2; margin: 3; margin-top: 1; padding: 2; padding-left: 3 !important; height: 8}</style></head><body><p style="height: 8; padding: 2">quirks</p><table class="worksheet" style="width: 120px"><colgroup><col><col></colgroup><tr><td class="sheet-column-axis"></td><td>A</td></tr></table><table class="worksheet" style="width: 50%"><col><tr><td class="sheet-column-axis"></td></tr></table><table class="worksheet"><tr><td>plain</td></tr></table><div class="boundary-nav">stay</div></body></html>`;
    const extracted = `<!DOCTYPE html><html><body><div class="boundary-nav">nav</div><div class="boundary-note">note</div><p>extracted body</p></body></html>`;
    const pages: Record<string, string> = {
      "/view/job/pages/1": rich,
      "/view/job/pages/2": quirks,
      "/view/job/extracted": extracted,
      "/files/job/extra.css":
        '@import url("/files/job/imported.css");\n@namespace url(http://www.w3.org/1999/xhtml);\n',
    };
    const seen: string[] = [];
    installFetch((url) => {
      seen.push(`${url.pathname}?${url.searchParams}`);
      const body = pages[url.pathname];
      if (!body) return textResponse("missing", 404);
      return textResponse(body);
    });
    const decoded: string[] = [];
    HTMLImageElement.prototype.decode = function () {
      decoded.push(this.src);
      return Promise.resolve();
    };
    const client = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "clientWidth",
    );
    const scroll = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "scrollWidth",
    );
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get() {
        return 100;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
      configurable: true,
      get() {
        return 400;
      },
    });
    const realStyle = window.getComputedStyle.bind(window);
    const same = new URL(document.URL);
    same.hash = "section";
    vi.spyOn(window, "getComputedStyle").mockImplementation((node: Element) => {
      const style = realStyle(node);
      if (!(node instanceof Element) || !node.classList.contains("asset-probe"))
        return style;
      return new Proxy(style, {
        get(target, prop, receiver) {
          if (prop === "backgroundImage")
            return `url("${location.origin}/pic.png")`;
          if (prop === "maskImage") return 'url("#glyph")';
          if (prop === "borderImageSource") return `url("${same.href}")`;
          if (prop === "listStyleImage")
            return 'url("https://evil.example/x.png")';
          const value = Reflect.get(target, prop, receiver);
          return typeof value === "function" ? value.bind(target) : value;
        },
      });
    });
    const printUnitsForRender: PrintUnit[] = [
      { number: 1, url: "/view/job/pages/1", width: 640, height: 480 },
      { number: 2, url: "/view/job/pages/2" },
      { number: 3, url: "/view/job/extracted", extracted: true },
    ];
    const settings: PrintSettings = {
      paper: "source",
      landscape: false,
      margin: 10,
    };
    try {
      await renderPrintDocument(
        document,
        { filename: "report.pdf" },
        printUnitsForRender,
        settings,
        "ja",
        "r1",
        signal(),
      );
      expect(document.title).toBe("report.pdf");
      const css = [...document.head.querySelectorAll("style")]
        .map((node) => node.textContent || "")
        .join("\n");
      expect(css).toContain("size:640px 480px");
      expect(css).toContain("size:793.7008px 1122.5197px");
      expect(css.match(/font-family: One/g)).toHaveLength(1);
      expect(css).toContain("font-family: Two");
      const sections = [...document.querySelectorAll<HTMLElement>(".print-unit")];
      expect(sections.map((section) => section.dataset.number)).toEqual([
        "1",
        "2",
        "3",
      ]);
      const fixed = sections[0].firstElementChild!.shadowRoot!;
      expect(fixed.querySelector("style")?.textContent).toContain(":host");
      expect(fixed.querySelector("style")?.textContent).toContain(".print-body");
      expect(fixed.querySelector(".print-body")?.getAttribute("style")).toContain(
        "transform",
      );
      const flow = sections[1].firstElementChild!.shadowRoot!;
      const flowBody = flow.querySelector<HTMLElement>(".print-body")!;
      expect(flowBody.style.zoom).toBe("0.25");
      expect(flowBody.querySelector("p")?.getAttribute("style")).toContain(
        "height: 8px",
      );
      const tables = [...flowBody.querySelectorAll<HTMLTableElement>("table.worksheet")];
      expect(tables[0].querySelectorAll("col")).toHaveLength(1);
      expect(tables[0].style.width).toBe("76px");
      expect(tables[0].querySelector(".sheet-column-axis")).toBeNull();
      expect(tables[1].querySelector("col")).toBeNull();
      expect(tables[1].style.width).toBe("50%");
      expect(tables[2].textContent).toContain("plain");
      expect(flowBody.textContent).toContain("stay");
      const extractedBody = sections[2].firstElementChild!.shadowRoot!;
      expect(extractedBody.querySelector(".boundary-nav,.boundary-note")).toBeNull();
      expect(extractedBody.textContent).toContain("extracted body");
      expect(decoded).toEqual(
        expect.arrayContaining([
          new URL("/files/job/photo.png", location.origin).href,
          new URL("/files/job/vector.png", location.origin).href,
          new URL("/files/job/linked.png", location.origin).href,
          new URL("/pic.png", location.origin).href,
        ]),
      );
      expect(decoded.join(" ")).not.toContain("evil.example");
      expect(decoded.join(" ")).not.toContain("#section");
      expect(decoded.join(" ")).not.toContain("#glyph");
      expect(seen.some((url) => url.startsWith("/view/job/pages/1?"))).toBe(true);
      const fixedUrl = new URL(seen.find((url) => url.includes("/pages/1"))!, location.origin);
      expect(fixedUrl.searchParams.get("language")).toBe("ja");
      expect(fixedUrl.searchParams.get("revision")).toBe("r1");
      const extractedUrl = new URL(
        seen.find((url) => url.includes("/extracted"))!,
        location.origin,
      );
      expect(extractedUrl.searchParams.get("language")).toBe("original");
    } finally {
      if (client)
        Object.defineProperty(HTMLElement.prototype, "clientWidth", client);
      else Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
      if (scroll)
        Object.defineProperty(HTMLElement.prototype, "scrollWidth", scroll);
      else Reflect.deleteProperty(HTMLElement.prototype, "scrollWidth");
    }
    installFetch((url) => {
      if (url.pathname === "/view/job/pages/9")
        return textResponse("<!DOCTYPE html><html><body><p>letter</p></body></html>");
      return textResponse("missing", 404);
    });
    await renderPrintDocument(
      document,
      { filename: "letter.pdf" },
      [{ number: 9, url: "/view/job/pages/9", width: 200, height: 100 }],
      { paper: "letter", landscape: true, margin: 20 },
      "en",
      "r2",
      signal(),
    );
    expect(
      [...document.head.querySelectorAll("style")]
        .map((node) => node.textContent)
        .join("\n"),
    ).toContain("size:1056px 816px");
    const controller = new AbortController();
    controller.abort(new Error("stopped"));
    installFetch(() => textResponse("<p>late</p>"));
    await expect(
      renderPrintDocument(
        document,
        { filename: "late.pdf" },
        [{ number: 1, url: "/view/job/late" }],
        { paper: "a4", landscape: false, margin: 0 },
        "original" satisfies Language,
        "r3",
        controller.signal,
      ),
    ).rejects.toThrow("stopped");
    installFetch(() => textResponse("nope", 503));
    await expect(
      renderPrintDocument(
        document,
        { filename: "late.pdf" },
        [{ number: 1, url: "/view/job/late" }],
        { paper: "a4", landscape: false, margin: 0 },
        "original",
        "r3",
        signal(),
      ),
    ).rejects.toThrow("印刷用の文書を読み込めませんでした（HTTP 503）");
  });
});
