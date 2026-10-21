import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "vitest";
import {
  familyOf,
  isDocument,
  isExcalidraw,
  isSupported,
  isWikiArticle,
  isWikiImportFile,
  matchesFormat,
  originalName,
  supportedAccept,
  supportedLabel,
  viewKind,
} from "./formats";

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
  for (const name of [
    "image.svg.exe",
    "image.bmp",
    "image.tiff",
    "image.avif",
    "image.png.exe",
  ]) {
    expect(isSupported(name)).toBe(false);
    expect(matchesFormat(name, "image")).toBe(false);
  }
});

test("accepts standalone TeX documents and their format filter", () => {
  for (const name of ["paper.TeX", "paper.LaTeX"]) {
    expect(isSupported(name)).toBe(true);
    expect(isDocument(name)).toBe(true);
    expect(matchesFormat(name, "tex")).toBe(true);
    expect(matchesFormat(name, "txt")).toBe(false);
  }
  expect(isSupported("paper.tex.exe")).toBe(false);
});

test("accepts native draw.io containers and filters diagrams", () => {
  for (const name of [
    "graph.DRAWIO",
    "graph.dio",
    "graph.xml",
    "graph.drawio.svg",
    "graph.drawio.png",
  ]) {
    expect(isSupported(name)).toBe(true);
    expect(matchesFormat(name, "drawio")).toBe(true);
  }
  expect(matchesFormat("graph.txt", "drawio")).toBe(false);
});

test("accepts standalone Excalidraw files and filters them", () => {
  expect(supportedAccept).toContain(".excalidraw");
  expect(supportedLabel).toContain("Excalidraw");
  for (const name of ["scene.excalidraw", "Scene.EXCALIDRAW"]) {
    expect(isSupported(name)).toBe(true);
    expect(isExcalidraw(name)).toBe(true);
    expect(isDocument(name)).toBe(false);
    expect(matchesFormat(name, "excalidraw")).toBe(true);
    expect(matchesFormat(name, "drawio")).toBe(false);
  }
  expect(isSupported("scene.excalidraw.exe")).toBe(false);
  expect(isSupported("scene.excalidraw.json")).toBe(false);
  expect(matchesFormat("graph.drawio", "excalidraw")).toBe(false);
  expect(supportedAccept).toContain(".drawio");
});

// The same file the server-side test (tests/test_format_contract.py) reads.
const contract = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../tests/fixtures/format-contract/cases.json"),
    "utf8",
  ),
) as {
  names: { name: string; family: string | null }[];
  content_cases: { name: string; diagram: boolean }[];
};

test("files the shared boundary names under the family the server uses", () => {
  expect(contract.names.length).toBeGreaterThan(30);
  for (const { name, family } of contract.names) {
    expect(familyOf(name), name).toBe(family);
    expect(isSupported(name), name).toBe(family !== null);
  }
});

test("every extension offered to the file chooser is accepted, and nothing else is", () => {
  const offered = supportedAccept.split(",");
  for (const extension of offered) {
    expect(isSupported(`file${extension}`), extension).toBe(true);
    expect(isSupported(extension), extension).toBe(false);
  }
  expect(new Set(offered).size).toBe(offered.length);
  expect(
    contract.names
      .filter((n) => n.family)
      .every((n) =>
        offered.some((extension) => n.name.toLowerCase().endsWith(extension)),
      ),
  ).toBe(true);
});

test("a name does not decide how a stored document is opened, the server's flags do", () => {
  // A PNG that carries a draw.io model is a diagram, an ordinary one an image.
  for (const { name, diagram } of contract.content_cases) {
    expect(
      viewKind({ diagram, image: diagram ? undefined : { reference: name } }),
      name,
    ).toBe(diagram ? "drawio" : "image");
  }
  expect(viewKind({ excalidraw: true })).toBe("excalidraw");
  expect(viewKind({})).toBe("extracted");
  // Extraction can have failed or been skipped: no flags, whatever the name.
  expect(viewKind({ diagram: false, image: null })).toBe("extracted");
});

test("the original file name is the one the person uploaded", () => {
  expect(originalName({ filename: "a.pdf", original_filename: "b.tex" })).toBe(
    "b.tex",
  );
  expect(originalName({ filename: "a.pdf", original_filename: null })).toBe(
    "a.pdf",
  );
  expect(originalName({ filename: "a.pdf", original_filename: "" })).toBe(
    "a.pdf",
  );
});

test("Wiki import takes Markdown and CSV articles and the two manifest files", () => {
  for (const name of ["a.md", "A.MARKDOWN", "index.csv"]) {
    expect(isWikiArticle(name), name).toBe(true);
    expect(isWikiImportFile(name), name).toBe(true);
  }
  for (const name of ["wiki-manifest.json", "pages.jsonl"]) {
    expect(isWikiArticle(name), name).toBe(false);
    expect(isWikiImportFile(name), name).toBe(true);
  }
  for (const name of [
    "a.txt",
    "manifest.json",
    "pages.json",
    ".md",
    "a.md.bak",
  ])
    expect(isWikiImportFile(name), name).toBe(false);
});
