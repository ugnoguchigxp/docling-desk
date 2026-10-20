import { afterEach, beforeEach, expect, test, vi } from "vitest";

const calls = vi.hoisted(() => ({
  canvas: [] as Array<Record<string, unknown>>,
  svg: [] as Array<Record<string, unknown>>,
}));
vi.mock("@excalidraw/excalidraw", () => ({
  restore: (data: {
    elements: unknown[];
    appState?: object;
    files?: object;
  }) => ({
    elements: data.elements,
    appState: { viewBackgroundColor: "#ffffff", ...data.appState },
    files: data.files ?? {},
  }),
  exportToSvg: async (options: Record<string, unknown>) => {
    calls.svg.push(options);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "10");
    svg.setAttribute("viewBox", "0 0 10 10");
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.textContent = "日本語";
    svg.append(text);
    return svg;
  },
  exportToCanvas: async (options: {
    getDimensions: (w: number, h: number) => unknown;
  }) => {
    calls.canvas.push(options);
    const size = options.getDimensions(5000, 2000) as {
      width: number;
      height: number;
    };
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    return canvas;
  },
}));

import {
  exportHtml,
  exportRaster,
  exportSvg,
  fitExport,
  prepareScene,
  safeLink,
} from "./export";

const rect = (extra: object = {}) => ({
  id: "a",
  type: "rectangle",
  isDeleted: false,
  link: null,
  ...extra,
});
const scene = (elements: object[], files: object = {}) =>
  JSON.stringify({ type: "excalidraw", version: 2, elements, files });
const read = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });

beforeEach(() => {
  calls.canvas.length = 0;
  calls.svg.length = 0;
});
afterEach(() => vi.restoreAllMocks());

test("fitExport keeps scale 2 for small scenes", () => {
  expect(fitExport(300, 200)).toEqual({ width: 600, height: 400, scale: 2 });
});

test("fitExport limits the long edge to 4096px", () => {
  const out = fitExport(5000, 1000);
  expect(out.width).toBeLessThanOrEqual(4096);
  expect(out.width).toBe(4096);
  expect(out.scale).toBeCloseTo(4096 / 5000);
});

test("fitExport limits the area to 16 megapixels", () => {
  // 4000x4000 at scale 2 would be 64MP and 8000px.
  const out = fitExport(4000, 4000);
  expect(out.width * out.height).toBeLessThanOrEqual(16_000_000);
  expect(out.width).toBeLessThanOrEqual(4096);
  expect(out.width).toBe(4000);
  // Both limits at once: edge is fine, area is not.
  const wide = fitExport(3000, 2500, { scale: 2 });
  expect(wide.width * wide.height).toBeLessThanOrEqual(16_000_000);
  expect(Math.max(wide.width, wide.height)).toBeLessThanOrEqual(4096);
});

test("fitExport never returns an empty size", () => {
  expect(fitExport(0, 0)).toMatchObject({ width: 2, height: 2 });
  const tiny = fitExport(100_000, 1);
  expect(tiny.width).toBe(4096);
  expect(tiny.height).toBeGreaterThanOrEqual(1);
});

test("safeLink only passes http, https and mailto", () => {
  expect(safeLink("https://example.com/a")).toBe("https://example.com/a");
  expect(safeLink("mailto:a@example.com")).toBe("mailto:a@example.com");
  expect(safeLink("javascript:alert(1)")).toBeNull();
  expect(safeLink("data:text/html,x")).toBeNull();
  expect(safeLink("not a url")).toBeNull();
  expect(safeLink("")).toBeNull();
  expect(safeLink(null)).toBeNull();
});

test("prepareScene drops deleted, unsupported and unsafe parts", async () => {
  const prepared = await prepareScene(
    scene(
      [
        rect({ id: "ok", link: "https://example.com" }),
        rect({ id: "gone", isDeleted: true }),
        rect({ id: "embed", type: "embeddable" }),
        rect({ id: "bad", link: "javascript:alert(1)" }),
        { id: "i1", type: "image", fileId: "f1", isDeleted: false },
        { id: "i2", type: "image", fileId: "f2", isDeleted: false },
      ],
      {
        f1: {
          id: "f1",
          mimeType: "image/png",
          dataURL: "data:image/png;base64,AA==",
        },
        f2: {
          id: "f2",
          mimeType: "image/svg+xml",
          dataURL: "data:image/svg+xml;base64,AA==",
        },
      },
    ),
  );
  expect(prepared.elements.map((e) => e.id)).toEqual(["ok", "bad", "i1"]);
  expect(prepared.elements[0]?.link).toBe("https://example.com");
  expect(prepared.elements[1]?.link).toBeNull();
  expect(Object.keys(prepared.files)).toEqual(["f1"]);
});

test("an empty scene cannot be exported", async () => {
  await expect(exportSvg(scene([]))).rejects.toThrow(
    "書き出す図形がありません",
  );
});

test("exportSvg returns an image/svg+xml blob", async () => {
  const blob = await exportSvg(scene([rect()]));
  expect(blob.type).toBe("image/svg+xml");
  const text = await read(blob);
  expect(text).toContain("<svg");
  expect(text).toContain("日本語");
  expect(calls.svg[0]?.exportPadding).toBe(10);
});

test("exportHtml embeds the SVG as data without script and sets the CSP", async () => {
  const blob = await exportHtml(scene([rect()]), 'A<b>"&');
  expect(blob.type).toBe("text/html");
  const html = await read(blob);
  expect(html).toContain(
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">`,
  );
  expect(html).not.toMatch(/<script/i);
  expect(html).toContain("<title>A&lt;b&gt;&quot;&amp;</title>");
  const match = /src="data:image\/svg\+xml;base64,([^"]+)"/.exec(html);
  expect(match).not.toBeNull();
  const svg = new TextDecoder().decode(
    Uint8Array.from(atob(match![1]!), (c) => c.charCodeAt(0)),
  );
  expect(svg).toContain("日本語");
});

function stubEncoder(supportedWebp: boolean) {
  const encoded: Array<{ type: string; quality: number; white: boolean }> = [];
  const fills: string[] = [];
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () =>
      ({
        set fillStyle(value: string) {
          fills.push(value);
        },
        fillRect: vi.fn(),
        drawImage: vi.fn(),
      }) as unknown as CanvasRenderingContext2D,
  );
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
    function (callback, type, quality) {
      encoded.push({
        type: String(type),
        quality: Number(quality),
        white: fills.includes("#ffffff"),
      });
      const png = type === "image/webp" && !supportedWebp;
      callback(new Blob(["x"], { type: png ? "image/png" : String(type) }));
    },
  );
  return encoded;
}

test("exportRaster encodes WebP at 0.85 within the size limits", async () => {
  const encoded = stubEncoder(true);
  const result = await exportRaster(scene([rect()]), "webp");
  expect(result.format).toBe("webp");
  expect(result.blob.type).toBe("image/webp");
  expect(encoded).toEqual([
    { type: "image/webp", quality: 0.85, white: false },
  ]);
  const size = calls.canvas[0]!.getDimensions as (
    w: number,
    h: number,
  ) => { width: number };
  expect(size(5000, 2000).width).toBe(4096);
});

test("exportRaster composites JPEG on white at 0.85", async () => {
  const encoded = stubEncoder(true);
  const result = await exportRaster(scene([rect()]), "jpeg");
  expect(result.format).toBe("jpeg");
  expect(encoded).toEqual([{ type: "image/jpeg", quality: 0.85, white: true }]);
});

test("exportRaster falls back to JPEG when WebP is unsupported", async () => {
  const encoded = stubEncoder(false);
  const result = await exportRaster(scene([rect()]), "webp");
  expect(result.format).toBe("jpeg");
  expect(result.blob.type).toBe("image/jpeg");
  expect(encoded.map((e) => e.type)).toEqual(["image/webp", "image/jpeg"]);
});
