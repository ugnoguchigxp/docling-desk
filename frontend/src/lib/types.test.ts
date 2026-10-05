import { expect, test } from "vitest";
import { isDone, itemName, unitLabel, type Item } from "./types";

test("labels units, completion, and item names", () => {
  expect(unitLabel("document", 3)).toBe("文書全体");
  expect(unitLabel("page", 2)).toBe("ページ 2");
  expect(unitLabel("slide", 1)).toBe("スライド 1");
  expect(unitLabel("sheet", 4)).toBe("シート 4");
  expect(isDone(undefined)).toBe(false);
  expect(isDone(null)).toBe(false);
  expect(isDone({ state: "success" } as never)).toBe(true);
  expect(isDone({ state: "partial" } as never)).toBe(true);
  expect(isDone({ state: "failed" } as never)).toBe(false);
  const file: Item = { kind: "file", filename: "a.pdf" } as Item;
  const folder: Item = { kind: "folder", name: "議事" } as Item;
  expect(itemName(file)).toBe("a.pdf");
  expect(itemName(folder)).toBe("議事");
});
