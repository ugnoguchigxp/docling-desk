import { afterEach, expect, test, vi } from "vitest";
import type { DocumentReference } from "../reference";
import { askHost, connectHost, reportHeight } from "./open-webui";

const reference: DocumentReference = {
  source_id: "s",
  source_revision: "r",
  evidence_revision: "e",
};

const setReferrer = (value: string) =>
  Object.defineProperty(document, "referrer", { value, configurable: true });
const setAncestors = (value?: string[]) =>
  Object.defineProperty(location, "ancestorOrigins", {
    value,
    configurable: true,
  });
afterEach(() => {
  vi.unstubAllGlobals();
  setReferrer("");
  setAncestors(undefined);
});

test("sends a bounded prompt, a connection code, and a clamped height", () => {
  const post = vi.fn();
  vi.stubGlobal("parent", { postMessage: post });
  setAncestors([location.origin]);
  expect(askHost("", reference)).toContain("資料参照");
  expect(askHost("", reference)).not.toContain("選択した本文");
  const long = "あ".repeat(10001);
  const prompt = askHost(long, reference);
  expect(prompt).toContain(long.slice(0, 10000));
  expect(prompt).not.toContain(long);
  connectHost("ABC");
  vi.stubGlobal("innerHeight", 400);
  reportHeight();
  vi.stubGlobal("innerHeight", 5000);
  reportHeight();
  vi.stubGlobal("innerHeight", 0);
  reportHeight();
  vi.stubGlobal("innerHeight", 800);
  reportHeight();
  const heights = post.mock.calls
    .filter((call) => call[0].type === "iframe:height")
    .map((call) => call[0].height);
  expect(heights).toEqual([600, 1000, 760, 800]);
  expect(post).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "input:prompt",
      text: expect.stringContaining("ABC"),
    }),
    location.origin,
  );
  expect(post.mock.calls.every((call) => call[1] === location.origin)).toBe(
    true,
  );
});

test("addresses the embedding parent by origin and never sends when it is unknown", () => {
  const post = vi.fn();
  vi.stubGlobal("parent", { postMessage: post });
  // No ancestorOrigins and no referrer: the parent cannot be determined.
  connectHost("ABC");
  askHost("secret", reference);
  expect(post).not.toHaveBeenCalled();
  // Not framed at all.
  vi.stubGlobal("parent", window);
  setAncestors([location.origin]);
  connectHost("ABC");
  expect(post).not.toHaveBeenCalled();
});

test("falls back to the referrer origin, and reaches an opaque wrapper only inside a same-origin chain", () => {
  const post = vi.fn();
  vi.stubGlobal("parent", { postMessage: post });
  setReferrer("https://chat.example.com/c/1?x=2");
  connectHost("ABC");
  expect(post).toHaveBeenLastCalledWith(
    expect.anything(),
    "https://chat.example.com",
  );
  // Same-origin referrer: the parent may be a sandboxed (opaque) wrapper.
  setReferrer(location.origin + "/chat");
  connectHost("ABC");
  expect(post).toHaveBeenLastCalledWith(expect.anything(), "*");
  post.mockClear();
  // Opaque nearest parent inside a foreign chain is refused.
  setReferrer("");
  setAncestors(["null", "https://evil.example"]);
  askHost("secret", reference);
  expect(post).not.toHaveBeenCalled();
  setAncestors(["null", location.origin]);
  askHost("text", reference);
  expect(post).toHaveBeenLastCalledWith(expect.anything(), "*");
  setAncestors(["https://chat.example.com", location.origin]);
  reportHeight();
  expect(post).toHaveBeenLastCalledWith(
    expect.objectContaining({ type: "iframe:height" }),
    "https://chat.example.com",
  );
});
