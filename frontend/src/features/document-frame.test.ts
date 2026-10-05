import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const bridge = readFileSync(
  resolve(process.cwd(), "../src/docling_desk/resources/static/document-frame.js"),
  "utf8",
);
const dropBridge = readFileSync(
  resolve(process.cwd(), "../src/docling_desk/resources/static/file-drop.js"),
  "utf8",
);
let post: ReturnType<typeof vi.fn>;
let frameParent: { postMessage: ReturnType<typeof vi.fn> };
beforeEach(() => {
  document.body.innerHTML =
    '<p>日本語 Selected text</p><iframe title="child"></iframe>';
  document.body.dataset.job = "job";
  document.body.dataset.unit = "page-1";
  post = vi.fn();
  frameParent = { postMessage: post };
  vi.stubGlobal("postMessage", post);
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => {
    fn(0);
    return 1;
  });
  Object.defineProperty(document, "fonts", {
    configurable: true,
    value: { ready: Promise.resolve() },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function run(script: string) {
  // The bridge installs listeners. Use disposable windows to avoid accumulated listeners.
  const frame = document.querySelector("iframe")!;
  const w = frame.contentWindow! as Window & typeof globalThis;
  w.document.body.innerHTML =
    '<p>日本語 Selected text</p><iframe title="nested"></iframe>';
  w.document.body.dataset.job = "job";
  w.document.body.dataset.unit = document.body.dataset.unit;
  Object.defineProperty(w.document, "fonts", {
    value: { ready: Promise.resolve() },
  });
  w.requestAnimationFrame = (fn) => {
    fn(0);
    return 1;
  };
  // jsdom's iframe eval does not enter that frame's global scope. Bind the
  // bridge's browser globals explicitly to exercise the disposable frame.
  const execute = new Function(
    "window",
    "document",
    "parent",
    "getSelection",
    "requestAnimationFrame",
    script,
  );
  execute(
    w,
    w.document,
    frameParent,
    w.getSelection.bind(w),
    w.requestAnimationFrame,
  );
  return w;
}
describe("document frame bridge", () => {
  it("reports the same text again after clearing a selection", () => {
    const w = run(bridge);
    const selectAgain = () => {
      const range = w.document.createRange();
      range.selectNodeContents(w.document.querySelector("p")!);
      w.getSelection()!.addRange(range);
      w.document.dispatchEvent(new w.Event("selectionchange"));
    };
    selectAgain();
    const clear = new w.MessageEvent("message", {
      data: { jobId: "job", type: "docling-selection-clear" },
    });
    Object.defineProperty(clear, "source", { value: frameParent });
    w.dispatchEvent(clear);
    post.mockClear();
    selectAgain();
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({ text: "日本語 Selected text" }),
      "*",
    );
  });
  it("does not relay selections from a child hidden by its ancestor", () => {
    const w = run(bridge);
    w.document.body.innerHTML =
      '<section hidden><iframe title="inactive"></iframe></section>';
    const child = w.document.querySelector("iframe")!;
    w.dispatchEvent(
      new w.MessageEvent("message", {
        source: child.contentWindow,
        data: {
          jobId: "job",
          type: "docling-text-selection",
          unitId: "page-1",
          text: "Hidden page",
        },
      }),
    );
    expect(post).not.toHaveBeenCalled();
  });
  it("selects a worksheet cell on one click, including nested text and numbers", () => {
    document.body.dataset.unit = "sheet-1";
    const w = run(bridge);
    w.document.body.innerHTML =
      '<table class="worksheet"><tr><td><span>日本語 120</span></td><td>42</td></tr></table>';
    w.document
      .querySelector("span")!
      .dispatchEvent(new w.MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(w.getSelection()!.toString()).toBe("日本語 120");
    expect(post).toHaveBeenLastCalledWith(
      {
        jobId: "job",
        type: "docling-text-selection",
        unitId: "sheet-1",
        text: "日本語 120",
      },
      "*",
    );
    w.getSelection()!.removeAllRanges();
    w.document
      .querySelectorAll("td")[1]
      .dispatchEvent(new w.MouseEvent("click", { bubbles: true, detail: 1 }));
    expect(w.getSelection()!.toString()).toBe("42");
  });
  it("preserves worksheet substrings selected by dragging or double-clicking", () => {
    document.body.dataset.unit = "sheet-1";
    const w = run(bridge);
    w.document.body.innerHTML =
      '<table class="worksheet"><tr><td>日本語 Selected text</td></tr></table>';
    const cell = w.document.querySelector("td")!;
    const range = w.document.createRange();
    range.setStart(cell.firstChild!, 4);
    range.setEnd(cell.firstChild!, 12);
    w.getSelection()!.addRange(range);
    for (const detail of [1, 2]) {
      cell.dispatchEvent(new w.MouseEvent("click", { bubbles: true, detail }));
      expect(w.getSelection()!.toString()).toBe("Selected");
    }
  });
  it("leaves blank cells, links and modified clicks to the browser", () => {
    document.body.dataset.unit = "sheet-1";
    const w = run(bridge);
    w.document.body.innerHTML =
      '<table class="worksheet"><tr><td> </td><td><a href="#">Link</a></td><td>Value</td></tr></table>';
    const cells = w.document.querySelectorAll("td");
    for (const [node, options] of [
      [cells[0], {}],
      [w.document.querySelector("a")!, {}],
      [cells[2], { shiftKey: true }],
      [cells[2], { metaKey: true }],
    ] as const) {
      node.dispatchEvent(
        new w.MouseEvent("click", { bubbles: true, detail: 1, ...options }),
      );
      expect(w.getSelection()!.toString()).toBe("");
    }
    expect(post).not.toHaveBeenCalled();
  });
  it("measures Word after a hidden tab opens and applies only valid parent zoom requests", async () => {
    document.body.dataset.unit = "document-1";
    const w = run(bridge);
    await Promise.resolve();
    await Promise.resolve();
    expect(post).not.toHaveBeenCalled();
    vi.spyOn(
      w.document.querySelector("p")!,
      "getBoundingClientRect",
    ).mockReturnValue({ width: 600 } as DOMRect);
    const request = (type: string, ratio?: number) => {
      const event = new w.MessageEvent("message", {
        data: { jobId: "job", type, ratio },
      });
      Object.defineProperty(event, "source", { value: frameParent });
      w.dispatchEvent(event);
    };
    request("docling-document-request");
    expect(post).toHaveBeenCalledWith(
      { jobId: "job", type: "docling-document-size", width: 600 },
      "*",
    );
    request("docling-word-zoom", 1.5);
    expect(w.document.documentElement.style.zoom).toBe("1.5");
    request("docling-word-zoom", 10);
    expect(w.document.documentElement.style.zoom).toBe("1.5");
  });
  it("does not offer whitespace selected in the outer PDF/workbook shell", () => {
    delete document.body.dataset.unit;
    const w = run(bridge);
    w.document.body.removeAttribute("data-unit");
    const range = w.document.createRange();
    range.selectNodeContents(w.document.querySelector("p")!);
    w.getSelection()!.addRange(range);
    w.document.dispatchEvent(new w.Event("selectionchange"));
    expect(post).not.toHaveBeenCalled();
  });
  it("reports the actual selected substring from PDF/HTML text", () => {
    const w = run(bridge);
    const range = w.document.createRange();
    range.setStart(w.document.querySelector("p")!.firstChild!, 4);
    range.setEnd(w.document.querySelector("p")!.firstChild!, 12);
    w.getSelection()!.addRange(range);
    w.document.dispatchEvent(new w.Event("selectionchange"));
    expect(post).toHaveBeenCalledWith(
      {
        jobId: "job",
        type: "docling-text-selection",
        unitId: "page-1",
        text: "Selected",
      },
      "*",
    );
  });
  it("relays nested PDF/Excel selections only from known visible child frames", () => {
    const w = run(bridge);
    const child = w.document.querySelector("iframe")!;
    const data = {
      jobId: "job",
      type: "docling-text-selection",
      unitId: "page-1",
      text: "PDF paragraph\n120",
    };
    w.dispatchEvent(new w.MessageEvent("message", { source: window, data }));
    expect(post).not.toHaveBeenCalled();
    w.dispatchEvent(
      new w.MessageEvent("message", { source: child.contentWindow, data }),
    );
    expect(post).toHaveBeenCalledWith(data, "*");
    post.mockClear();
    child.hidden = true;
    w.dispatchEvent(
      new w.MessageEvent("message", { source: child.contentWindow, data }),
    );
    expect(post).not.toHaveBeenCalled();
  });
  it("keeps native copy keys and forwards file drops without consuming text drags", () => {
    document.body.dataset.unit = "slide-1";
    const w = run(bridge + "\n" + dropBridge);
    const copy = new w.KeyboardEvent("keydown", {
      key: "c",
      metaKey: true,
      cancelable: true,
    });
    w.document.dispatchEvent(copy);
    expect(copy.defaultPrevented).toBe(false);
    const textDrag = new w.Event("drop", { cancelable: true });
    Object.defineProperty(textDrag, "dataTransfer", {
      value: { types: ["text/plain"] },
    });
    w.dispatchEvent(textDrag);
    expect(textDrag.defaultPrevented).toBe(false);
    const drop = new w.Event("drop", { cancelable: true });
    const file = new File(["content"], "sample.docx");
    Object.defineProperty(drop, "dataTransfer", {
      value: { types: ["Files"], items: [], files: [file] },
    });
    w.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "docling-file-drop",
        action: "drop",
        files: [file],
        rejected: [],
      }),
      "*",
    );
  });
});
