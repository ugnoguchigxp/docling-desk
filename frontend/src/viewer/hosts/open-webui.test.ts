import { afterEach, expect, test, vi } from "vitest";
import type { DocumentReference } from "../reference";
import { askHost, connectHost, reportHeight } from "./open-webui";

const reference: DocumentReference = {
  source_id: "s",
  source_revision: "r",
  evidence_revision: "e",
};

afterEach(() => vi.unstubAllGlobals());

test("sends a bounded prompt, a connection code, and a clamped height", () => {
  const post = vi.fn();
  vi.stubGlobal("parent", { postMessage: post });
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
    expect.objectContaining({ type: "input:prompt", text: expect.stringContaining("ABC") }),
    "*",
  );
});
