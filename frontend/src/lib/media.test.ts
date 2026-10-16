import { expect, test } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import { assetId, imageMarkdown } from "./media";

test("copied Markdown preserves brackets, slashes, and an immutable local reference", () => {
  const alt = "構成 [全体] \\ 図";
  const reference = "/api/wiki/images/" + "a".repeat(64) + ".png";
  const tree = unified().use(remarkParse).parse(imageMarkdown(alt, reference));
  const block = tree.children[0];
  if (block.type !== "paragraph" || block.children[0].type !== "image")
    throw new Error("not an image");
  expect(block.children[0].alt).toBe(alt);
  expect(block.children[0].url).toBe(reference);
  expect(imageMarkdown("", reference)).toBe(`![](${reference})`);
  expect(assetId("550e8400e29b41d4a716446655440000")).toBe(
    "550e8400-e29b-41d4-a716-446655440000",
  );
});
