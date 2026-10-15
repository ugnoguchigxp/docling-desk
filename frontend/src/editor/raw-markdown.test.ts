import { describe, expect, it } from "vitest";
import { combineParts, rawParts, replacePart } from "./raw-markdown";
import { editedMarkdown, markdownEditor, valueSnapshot } from "./plate";

const source =
  "\ufeff---\r\ntitle: 原文\r\n---\r\n\r\n# 見出し\r\n\r\n本文 **強調** と😀。\r\n\r\n```mermaid\r\ngraph TD; A-->B\r\n```\r\n\r\n$$\r\nx^2\r\n$$\r\n\r\n:::message\r\n独自記法\r\n:::\r\n\r\n[ref]: https://example.com\r\n\r\n画像 ![x](knowledge-attachment:123) と [リンク][ref]\r\n\r\n<Component a={danger()} />\r\n";
describe("source preserving Plate adapter", () => {
  it("preserves BOM, CRLF and every unknown block through a no-edit Plate round trip", () => {
    const parts = rawParts(source);
    for (const part of parts.filter((p) => p.editable)) {
      const editor = markdownEditor(part.raw);
      expect(
        editedMarkdown(editor, part.raw, valueSnapshot(editor.children)),
      ).toBe(part.raw);
    }
    expect(combineParts(parts)).toBe(source);
    expect(parts.some((p) => p.kind === "yaml" && !p.editable)).toBe(true);
    expect(parts.some((p) => p.kind === "code" && !p.editable)).toBe(true);
  });
  it("applies a genuine Plate edit only to the chosen paragraph", () => {
    const parts = rawParts(source);
    const index = parts.findIndex((p) => p.raw.startsWith("本文"));
    const part = parts[index];
    const editor = markdownEditor(part.raw);
    const snapshot = valueSnapshot(editor.children);
    editor.tf.insertText("追加", { at: { path: [0, 0], offset: 0 } });
    const replacement = editedMarkdown(editor, part.raw, snapshot);
    const result = combineParts(
      replacePart(parts, index, part.raw, replacement),
    );
    expect(replacement).toContain("追加");
    expect(result).toBe(source.replace(part.raw, replacement));
    editor.tf.undo();
    expect(editedMarkdown(editor, part.raw, snapshot)).toBe(part.raw);
  });
  it("rejects editing protected blocks or stale text", () => {
    const parts = rawParts(source);
    expect(() => replacePart(parts, 0, parts[0].raw, "x")).toThrow();
    const i = parts.findIndex((p) => p.editable);
    expect(() => replacePart(parts, i, "stale", "x")).toThrow();
  });
});

it("source textarea edits preserve untouched CRLF and BOM", async () => {
  const { sourceEdit } = await import("./RawEditor");
  const original =
    "\ufeff---\r\ntitle: x\r\n---\r\n\r\n本文 😀\r\n\r\n```js\r\ncode\r\n```\r\n";
  expect(
    sourceEdit(
      original,
      original.replace(/\r\n/g, "\n").replace("本文", "変更"),
    ),
  ).toBe(original.replace("本文", "変更"));
});

it("retains checked task-list items while editing list text", () => {
  const parts = rawParts("- [x] done\n- [ ] later");
  const editor = markdownEditor(parts[0].raw);
  const initial = valueSnapshot(editor.children);
  editor.tf.insertText("edit ", { at: { path: [0, 0], offset: 0 } });
  const actual = editedMarkdown(editor, parts[0].raw, initial);
  expect(actual).toContain("[x]");
  expect(actual).toContain("[ ]");
});

it("split, merge and pasted text change only the editable block", () => {
  const original =
    "---\ntitle: keep\n---\n\nAlpha beta\n\n```mermaid\ngraph TD; A-->B\n```\n";
  const parts = rawParts(original);
  const index = parts.findIndex((part) => part.raw === "Alpha beta");
  const raw = parts[index].raw;
  const editor = markdownEditor(raw);
  const initial = valueSnapshot(editor.children);
  editor.tf.splitNodes({
    at: { path: [0, 0], offset: 6 },
    match: (n) => n.type === "p",
  });
  expect(editor.children).toHaveLength(2);
  let edited = editedMarkdown(editor, raw, initial);
  expect(edited).toContain("Alpha");
  expect(edited).toContain("beta");
  expect(combineParts(replacePart(parts, index, raw, edited))).toBe(
    original.replace(raw, edited),
  );
  editor.tf.mergeNodes({ at: [1] });
  expect(editor.children).toHaveLength(1);
  editor.tf.insertText(" pasted 😀\nnext line", {
    at: { path: [0, 0], offset: 0 },
  });
  edited = editedMarkdown(editor, raw, initial);
  expect(edited).toContain("pasted 😀");
  expect(edited).toContain("next line");
  expect(combineParts(replacePart(parts, index, raw, edited))).toBe(
    original.replace(raw, edited),
  );
});
