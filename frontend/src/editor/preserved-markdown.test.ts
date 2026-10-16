import { expect, it } from "vitest";
import { firstMarkdownImage, firstPlateImage } from "./raw-markdown";
import { markdownEditor } from "./plate";
import { preservedMarkdown } from "./preserved-markdown";

it("chooses the first real image, including references, excluding code and frontmatter", () => {
  const body =
    '\ufeff---\ncover: "![fake](https://example.com/front.png)"\n---\n\n```md\n![fake](https://example.com/code.png)\n```\n\n`![fake](https://example.com/inline.png)`\n\n> ![先頭][cover]\n\n![次](https://example.com/second.png)\n\n[cover]: https://example.com/first.png "説明"';
  expect(firstMarkdownImage(body)).toEqual({
    url: "https://example.com/first.png",
    alt: "先頭",
    caption: "説明",
  });
  expect(firstMarkdownImage("```md\n![fake](x)\n```")).toBeNull();
});

it("follows native document order inside columns and clears the candidate after deletion", () => {
  const value = [
    {
      type: "column_group",
      children: [
        {
          type: "column",
          children: [
            { type: "p", children: [{ text: "![fake](x)" }] },
            {
              type: "img",
              url: "https://example.com/one.png",
              alt: "一枚目",
              caption: [{ text: "補足" }],
              children: [{ text: "" }],
            },
          ],
        },
      ],
    },
    {
      type: "img",
      url: "https://example.com/two.png",
      children: [{ text: "" }],
    },
  ];
  expect(firstPlateImage(value)?.alt).toBe("一枚目");
  expect(firstPlateImage(value.slice(1))?.url).toContain("/two.png");
  expect(firstPlateImage([])).toBeNull();
});

it("preserves unknown blocks, original paragraph syntax, BOM and CRLF during another block edit", () => {
  const source =
    "\ufeff---\r\ncustom: keep\r\n---\r\n\r\nAlpha **bold**\r\n\r\nBeta\r\n\r\n<Component value={danger()} />\r\n";
  const editor = markdownEditor("");
  const adapter = preservedMarkdown(editor, source);
  editor.tf.setValue(adapter.value);
  expect(adapter.serialize(editor.children)).toBe(source);
  const index = editor.children.findIndex((node) =>
    node.children.some((c) => c.text === "Beta"),
  );
  editor.tf.insertText("new ", { at: { path: [index, 0], offset: 0 } });
  expect(adapter.serialize(editor.children)).toBe(
    source.replace("Beta", "new Beta"),
  );
});

it("uses a changed native caption over its original Markdown title", () => {
  expect(
    firstPlateImage([
      {
        type: "img",
        url: "https://example.com/p.png",
        alt: "",
        title: "Old",
        caption: [{ text: "New" }],
        children: [{ text: "" }],
      },
    ]),
  ).toEqual({ url: "https://example.com/p.png", alt: "", caption: "New" });
});

it("round-trips drawing data through the standard Markdown/MDX extension without executing expressions", async () => {
  const { createSlateEditor } = await import("platejs");
  const { MarkdownPlugin } = await import("@platejs/markdown");
  const { BaseExcalidrawPlugin } = await import("@platejs/excalidraw");
  const { MarkdownKit } =
    await import("../plate/components/editor/plugins/markdown-kit");
  const data = {
    elements: [{ id: "shape", type: "rectangle", x: 10, y: 20 }],
    state: { viewBackgroundColor: "#ffffff" },
    files: {},
  };
  const editor = createSlateEditor({
    plugins: [...MarkdownKit, BaseExcalidrawPlugin],
    value: [{ type: "excalidraw", data, children: [{ text: "" }] }],
  });
  const markdown = editor.getApi(MarkdownPlugin).markdown.serialize();
  expect(markdown).toContain("<excalidraw");
  expect(
    editor.getApi(MarkdownPlugin).markdown.deserialize(markdown)[0].data,
  ).toEqual(data);
});
