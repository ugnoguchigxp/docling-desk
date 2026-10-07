import { fileUrl, request } from "../lib/api";
import * as contracts from "../lib/contracts";
import { isDocument } from "../lib/formats";
import { withBase } from "../lib/base";
import {
  isLightweightPreview,
  type Job,
  type Language,
  type Slides,
} from "../lib/types";

async function readText(url: string, signal: AbortSignal) {
  const response = await fetch(withBase(url), { signal, cache: "no-store" });
  if (!response.ok)
    throw new Error(
      `印刷用の文書を読み込めませんでした（HTTP ${response.status}）。`,
    );
  return response.text();
}

export interface PrintUnit {
  number: number;
  url: string;
  width?: number;
  height?: number;
  extracted?: boolean;
}
export interface PrintSettings {
  paper: "source" | "a4" | "letter";
  landscape: boolean;
  margin: number;
}
export async function printUnits(
  job: Pick<
    Job,
    | "id"
    | "filename"
    | "original_filename"
    | "preview"
    | "slide_layout"
    | "pages"
  >,
  signal: AbortSignal,
): Promise<PrintUnit[]> {
  const base = `/view/${encodeURIComponent(job.id)}`;
  const name = (job.original_filename || job.filename).toLowerCase();
  const extracted = () => [
    { number: 1, url: `${base}/extracted`, extracted: true },
  ];
  // Extraction can succeed even when Office rendering is unavailable.
  if (!job.preview) return extracted();
  if (isDocument(name))
    return [
      {
        number: 1,
        url: `${base}/${name.endsWith(".docx") ? "word" : "document"}`,
      },
    ];
  if (name.endsWith(".pptx")) {
    const lightweight = isLightweightPreview(job);
    if (!job.slide_layout && !lightweight) return extracted();
    const data = await request<Slides>(
      fileUrl(job.id, "slides.json"),
      { signal },
      contracts.slides,
    );
    if (lightweight && !data.slides.some((s) => s.preview))
      throw new Error("印刷できるページがまだありません。");
    if (
      !data.slides.length ||
      (!lightweight && data.slides.some((s) => !s.preview))
    )
      return extracted();
    return data.slides
      .filter((s) => !!s.preview)
      .map((s) => ({
        number: s.number,
        url: lightweight
          ? fileUrl(job.id, s.preview!)
          : `${base}/slides/${s.number}`,
        width: s.width,
        height: s.height,
      }));
  }
  if (name.endsWith(".pdf")) {
    const raw = await readText(`${base}/pdf`, signal);
    const doc = new DOMParser().parseFromString(raw, "text/html");
    const units = [...doc.querySelectorAll<HTMLElement>(".pdf-page")].map(
      (p) => ({
        number: Number(p.dataset.number),
        url: `${base}/pages/${p.dataset.number}`,
        width: Number(p.dataset.width),
        height: Number(p.dataset.height),
      }),
    );
    if (
      !units.length ||
      units.some(
        (p) =>
          !Number.isFinite(p.width) ||
          !Number.isFinite(p.height) ||
          p.width <= 0 ||
          p.height <= 0,
      )
    )
      throw new Error("PDFのページ情報を読み込めませんでした。");
    return units;
  }
  if (name.endsWith(".xlsx") && job.pages > 0)
    return Array.from({ length: job.pages }, (_, i) => ({
      number: i + 1,
      url: `${base}/sheets/${i + 1}`,
    }));
  return extracted();
}

export function selectPrintUnits(
  units: PrintUnit[],
  scope: string,
  current: number,
  range: string,
) {
  if (scope === "all") return units;
  if (scope === "current") return units.filter((u) => u.number === current);
  const last = Math.max(0, ...units.map((u) => u.number));
  const numbers = new Set<number>();
  for (const part of range.split(/[,、]/)) {
    const match = /^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$/.exec(part);
    if (!match) throw new Error("範囲は「1-3, 5」のように入力してください。");
    const start = Number(match[1]),
      end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end > last)
      throw new Error(`範囲は1〜${last}で指定してください。`);
    for (let n = start; n <= end; n++) numbers.add(n);
  }
  return units.filter((u) => numbers.has(u.number));
}

function safeUrl(value: string, base: string) {
  value = value.trim();
  if (!value) return "";
  if (
    value.startsWith("#") ||
    /^data:(image|font|application\/font)/i.test(value)
  )
    return value;
  try {
    const url = new URL(value, base);
    return url.origin === location.origin && /^https?:$/.test(url.protocol)
      ? url.href
      : "";
  } catch {
    return "";
  }
}
function cssUrls(css: string, base: string) {
  return css.replace(
    /url\(\s*(['"]?)(.*?)\1\s*\)/gi,
    (_, _quote, value: string) => {
      const url = safeUrl(value, base);
      return url ? `url(${JSON.stringify(url)})` : "none";
    },
  );
}
export function passiveDocument(raw: string, base: string) {
  const doc = new DOMParser().parseFromString(raw, "text/html");
  doc
    .querySelectorAll("script,iframe,object,embed,base,form,meta[http-equiv]")
    .forEach((n) => n.remove());
  doc.querySelectorAll("*").forEach((node) => {
    if (node.localName === "img") node.setAttribute("loading", "eager");
    for (const attr of [...node.attributes]) {
      if (
        /^on/i.test(attr.name) ||
        [
          "srcdoc",
          "srcset",
          "action",
          "formaction",
          "target",
          "download",
        ].includes(attr.name)
      )
        node.removeAttribute(attr.name);
      else if (["src", "href", "xlink:href"].includes(attr.name)) {
        const value = safeUrl(attr.value, base);
        if (value) node.setAttribute(attr.name, value);
        else node.removeAttribute(attr.name);
      } else if (attr.name === "style")
        node.setAttribute("style", cssUrls(attr.value, base));
    }
    if (node.localName === "a") {
      node.removeAttribute("href");
      node.removeAttribute("xlink:href");
    }
  });
  return doc;
}

async function styleText(doc: Document, base: string, signal: AbortSignal) {
  return (
    await Promise.all(
      [...doc.querySelectorAll("style,link[rel=stylesheet]")].map(
        async (node) => {
          let css = node.textContent || "",
            url = base;
          if (node.localName === "link") {
            const href = node.getAttribute("href");
            if (!href) return "";
            url = new URL(href, base).href;
            css = await readText(url, signal);
          }
          return cssUrls(css, url);
        },
      ),
    )
  ).join("\n");
}

// Styles stay inside a shadow root so source class names and SVG IDs cannot
// collide between pages. Font faces must live in the containing document.
function scopedStyles(css: string, doc: Document) {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(css);
  const fonts: string[] = [];
  function rewrite(rules: CSSRuleList): string {
    return [...rules]
      .map((rule) => {
        if (rule instanceof CSSPageRule || rule instanceof CSSImportRule)
          return "";
        if (rule instanceof CSSFontFaceRule) {
          fonts.push(rule.cssText);
          return "";
        }
        if (rule instanceof CSSStyleRule) {
          const selector = rule.selectorText
            .replace(/(?<![\w-])(?:html|:root)(?![\w-])/g, ":host")
            .replace(/(?<![\w-])body(?![\w-])/g, ".print-body");
          return `${selector}{${rule.style.cssText}}`;
        }
        if ("cssRules" in rule) {
          const group = rule as CSSGroupingRule;
          return `${rule.cssText.slice(0, rule.cssText.indexOf("{"))}{${rewrite(group.cssRules)}}`;
        }
        // Original print page rules must not override the chosen paper.
        return rule.cssText;
      })
      .join("\n");
  }
  const scoped = rewrite(sheet.cssRules),
    style = doc.createElement("style");
  style.textContent = [...new Set(fonts)].join("\n");
  doc.head.append(style);
  return scoped;
}

function quirksDimensions(css: string) {
  return css.replace(
    /([{:;]\s*(?:(?:min-|max-)?(?:width|height)|font-size|text-indent|top|left|right|bottom|margin(?:-(?:top|left|right|bottom))?|padding(?:-(?:top|left|right|bottom))?)\s*:\s*)(-?\d+(?:\.\d+)?)(?=\s*(?:!important\s*)?(?:[;}]|$))/gi,
    "$1$2px",
  );
}

// Font and image decoding must respect cancellation, including a timeout or
// closing the preview while a resource is still loading.
export async function waitForPrintAssets(
  doc: Document,
  images: HTMLImageElement[],
  signal: AbortSignal,
) {
  signal.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const abort = () => fail(signal.reason);
    function fail(error: unknown) {
      signal.removeEventListener("abort", abort);
      reject(error);
    }
    signal.addEventListener("abort", abort, { once: true });
    const loading = images.map(async (img) => {
      if (!img.getAttribute("src")) return;
      await img.decode().catch(() => {
        throw new Error("印刷用の画像を読み込めませんでした。");
      });
      if (!img.naturalWidth)
        throw new Error("印刷用の画像を読み込めませんでした。");
    });
    Promise.all([doc.fonts.ready, ...loading]).then(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, fail);
  });
  signal.throwIfAborted();
}

export async function renderPrintDocument(
  doc: Document,
  job: Pick<Job, "filename">,
  units: PrintUnit[],
  settings: PrintSettings,
  language: Language,
  revision: string,
  signal: AbortSignal,
) {
  doc.title = job.filename;
  doc.body.replaceChildren();
  doc.head.querySelectorAll("style").forEach((s) => s.remove());
  const style = doc.createElement("style");
  style.textContent = `html,body{margin:0;padding:0;font-family:system-ui,sans-serif}body{background:#e2e8ec}*{box-sizing:border-box}.print-unit{background:white;position:relative;margin:20px auto;break-after:page;print-color-adjust:exact;-webkit-print-color-adjust:exact}.print-unit:last-child{break-after:auto}.print-fixed{overflow:hidden}.print-label{font-size:12px;padding:6px 0;color:#52625c}@media screen{body{padding:1px 20px}main{zoom:var(--preview-scale,1)}}@media print{body{background:white}.print-unit{margin:0!important;box-shadow:none}.print-label{display:none}.print-flow{min-height:0!important}}`;
  doc.head.append(style);
  const main = doc.createElement("main");
  doc.body.append(main);
  // Bound work to two pages at a time, and retain source order.
  const loaded: { unit: PrintUnit; source: Document; css: string }[] = [];
  for (let i = 0; i < units.length; i += 2) {
    const batch = await Promise.all(
      units.slice(i, i + 2).map(async (unit) => {
        const url = new URL(withBase(unit.url), location.origin);
        url.searchParams.set(
          "language",
          unit.extracted ? "original" : language,
        );
        url.searchParams.set("revision", revision);
        const raw = await readText(url.href, signal);
        const source = passiveDocument(raw, url.href);
        if (unit.extracted)
          source
            .querySelectorAll(".boundary-nav,.boundary-note")
            .forEach((n) => n.remove());
        return { unit, source, css: await styleText(source, url.href, signal) };
      }),
    );
    loaded.push(...batch);
  }
  signal.throwIfAborted();
  for (const { unit, source, css } of loaded) {
    const fixed = !!unit.width && !!unit.height;
    let [width, height] =
      settings.paper === "letter" ? [816, 1056] : [793.7008, 1122.5197];
    if (settings.landscape) [width, height] = [height, width];
    if (fixed && settings.paper === "source")
      [width, height] = [unit.width!, unit.height!];
    const margin =
      fixed && settings.paper === "source" ? 0 : (settings.margin * 96) / 25.4;
    const contentWidth = width - margin * 2,
      contentHeight = height - margin * 2;
    const pageName = `unit${unit.number}`;
    style.textContent += `@page ${pageName}{size:${width}px ${height}px;margin:${margin}px}`;
    const section = doc.createElement("section");
    section.className = `print-unit ${fixed ? "print-fixed" : "print-flow"}`;
    section.dataset.number = String(unit.number);
    section.style.cssText = `page:${pageName};width:${contentWidth}px;${fixed ? `height:${contentHeight}px;` : `min-height:${contentHeight}px;`}`;
    const host = doc.createElement("div"),
      shadow = host.attachShadow({ mode: "open" });
    const scoped = doc.createElement("style");
    const sourceCss = source.doctype ? css : quirksDimensions(css);
    scoped.textContent =
      scopedStyles(sourceCss, doc) +
      `\n:host{display:block!important;position:relative!important;margin:0!important;padding:0!important;background:white!important}.print-body{margin:0!important;padding:0!important;background:white!important;overflow:visible!important;height:auto!important} .sheet-column-axis,.sheet-row-axis{display:none!important}table.worksheet th{position:static!important}pre{white-space:pre-wrap;overflow-wrap:anywhere}.page{width:100%!important;max-width:none!important;padding:0!important;margin:0!important}img{max-width:100%}.extracted-page:last-child{break-after:auto}`;
    shadow.append(scoped);
    const body = doc.createElement("div");
    for (const attr of [...source.body.attributes])
      body.setAttribute(attr.name, attr.value);
    body.classList.add("print-body");
    body.append(
      ...[...source.body.childNodes].map((n) => doc.importNode(n, true)),
    );
    body
      .querySelectorAll("style,link[rel=stylesheet]")
      .forEach((node) => node.remove());
    if (!source.doctype)
      body.querySelectorAll<HTMLElement>("[style]").forEach((node) => {
        const raw = node.getAttribute("style") || "";
        node.setAttribute("style", quirksDimensions(";" + raw).slice(1));
      });
    // The viewer inserts a column for row numbers. Hiding its cells alone
    // shifts the original column widths by one position.
    for (const table of body.querySelectorAll<HTMLTableElement>(
      "table.worksheet",
    )) {
      if (!table.querySelector(".sheet-column-axis")) continue;
      table.querySelector("col")?.remove();
      if (table.style.width.endsWith("px"))
        table.style.width = `${Math.max(0, parseFloat(table.style.width) - 44)}px`;
      table
        .querySelectorAll(".sheet-column-axis,.sheet-row-axis")
        .forEach((n) => n.remove());
    }
    shadow.append(body);
    section.append(host);
    main.append(section);
    if (fixed) {
      host.style.width = `${contentWidth}px`;
      host.style.height = `${contentHeight}px`;
      const scale = Math.min(
        contentWidth / unit.width!,
        contentHeight / unit.height!,
      );
      body.style.cssText = `width:${unit.width}px!important;height:${unit.height}px!important;transform:scale(${scale});transform-origin:top left;position:absolute;left:${(contentWidth - unit.width! * scale) / 2}px;top:${(contentHeight - unit.height! * scale) / 2}px`;
    } else {
      body.style.width = `${contentWidth}px`;
    }
  }
  const images = [...main.children].flatMap((s) => [
    ...s.firstElementChild!.shadowRoot!.querySelectorAll("img"),
  ]);
  const imageUrls = new Set<string>();
  for (const section of main.children) {
    const shadow = section.firstElementChild!.shadowRoot!;
    for (const node of shadow.querySelectorAll("*")) {
      if (node.localName === "image") {
        const url =
          node.getAttribute("href") || node.getAttribute("xlink:href");
        if (url && !url.startsWith("#")) imageUrls.add(url);
      }
      const style = doc.defaultView!.getComputedStyle(node);
      for (const value of [
        style.backgroundImage,
        style.maskImage,
        style.borderImageSource,
        style.listStyleImage,
      ]) {
        for (const match of value.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)) {
          const url = safeUrl(match[2], location.origin);
          if (!url || url.startsWith("#")) continue;
          const resolved = new URL(url, doc.URL);
          if (
            resolved.hash &&
            resolved.href.split("#")[0] === doc.URL.split("#")[0]
          )
            continue;
          imageUrls.add(url);
        }
      }
    }
  }
  for (const url of imageUrls) {
    const probe = doc.createElement("img");
    probe.src = url;
    images.push(probe);
  }
  await waitForPrintAssets(doc, images, signal);
  // Wide worksheets fit the paper width and can continue over multiple pages.
  for (const section of main.querySelectorAll<HTMLElement>(".print-flow")) {
    const body =
      section.firstElementChild!.shadowRoot!.querySelector<HTMLElement>(
        ".print-body",
      )!;
    const scale = Math.min(1, section.clientWidth / body.scrollWidth);
    body.style.zoom = String(scale);
  }
}
