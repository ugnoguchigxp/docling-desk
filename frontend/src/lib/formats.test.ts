import { expect, test } from "vitest";
import { isDocument, isSupported, matchesFormat } from "./formats";

test("recognizes documents, supported uploads, and format filters", () => {
  expect(isDocument("Note.DOCX")).toBe(true);
  expect(isDocument("note.md")).toBe(true);
  expect(isDocument("note.markdown")).toBe(true);
  expect(isDocument("note.TXT")).toBe(true);
  expect(isDocument("note.text")).toBe(true);
  expect(isDocument("deck.pptx")).toBe(false);
  expect(isSupported("file.PDF")).toBe(true);
  expect(isSupported("file.pptx")).toBe(true);
  expect(isSupported("file.xlsx")).toBe(true);
  expect(isSupported("file.docx")).toBe(true);
  expect(isSupported("file.txt")).toBe(true);
  expect(matchesFormat("A.MD", "md")).toBe(true);
  expect(matchesFormat("A.markdown", "md")).toBe(true);
  expect(matchesFormat("A.pdf", "md")).toBe(false);
  expect(matchesFormat("A.TXT", "txt")).toBe(true);
  expect(matchesFormat("A.text", "txt")).toBe(true);
  expect(matchesFormat("A.md", "txt")).toBe(false);
  expect(matchesFormat("Deck.PPTX", "pptx")).toBe(true);
  expect(matchesFormat("Deck.pptx.bak", "pptx")).toBe(false);
});

test("accepts supported image uploads and filters their filename extensions", () => {
  for (const extension of ["PNG", "JPG", "JPEG", "GIF", "WEBP"]) {
    const name = `image.${extension}`;
    expect(isSupported(name)).toBe(true);
    expect(isDocument(name)).toBe(false);
    expect(matchesFormat(name, "image")).toBe(true);
  }
  expect(matchesFormat("photo.JPEG", "jpg")).toBe(true);
  expect(matchesFormat("photo.JPG", "jpg")).toBe(true);
  expect(matchesFormat("image.png", "jpg")).toBe(false);
  expect(matchesFormat("report.pdf", "image")).toBe(false);
});

test("rejects unsupported uploads and filenames with a disguised image extension", () => {
  for (const name of ["image.svg", "image.bmp", "image.tiff", "image.avif", "image.png.exe"]) {
    expect(isSupported(name)).toBe(false);
    expect(matchesFormat(name, "image")).toBe(false);
  }
});
