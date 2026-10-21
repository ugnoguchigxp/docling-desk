import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const src = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const staticStyle = resolve(
  src,
  "../../src/docling_desk/resources/static/style.css",
);
const read = (file: string) => readFileSync(file, "utf8");

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (path === join(src, "plate")) return []; // vendored, never edited
    return statSync(path).isDirectory()
      ? cssFiles(path)
      : path.endsWith(".css")
        ? [path]
        : [];
  });
}
const defined = (css: string) =>
  new Set([...css.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]));

describe("app tokens", () => {
  const tokens = read(join(src, "components/app-tokens.css"));

  it("agrees with the legacy stylesheet on the two values both define", () => {
    for (const name of ["--accent", "--line"]) {
      const pattern = new RegExp(`${name}:\\s*(#[0-9a-f]{3,8})`, "i");
      expect(read(staticStyle).match(pattern)?.[1]).toBeTruthy();
      expect(read(staticStyle).match(pattern)?.[1]).toBe(
        tokens.match(pattern)?.[1],
      );
    }
  });

  it("defines every token the app styles use, so no name is a typo", () => {
    const known = new Set([...defined(tokens), ...defined(read(staticStyle))]);
    const used = new Map<string, string>();
    for (const file of [...cssFiles(src), staticStyle])
      for (const m of read(file).matchAll(/var\((--[a-z0-9-]+)/g))
        if (!used.has(m[1])) used.set(m[1], file);
    const missing = [...used].filter(
      ([name, file]) =>
        !known.has(name) &&
        // file-local custom properties are declared in the same file
        !defined(read(file)).has(name),
    );
    expect(missing).toEqual([]);
  });

  it("reaches the app, the embed entry and the standalone editor", () => {
    const loads = (file: string) =>
      /import\s+"(\.\.?\/)+(components\/)?app-tokens\.css";|import\s+"\.\/app-tokens\.css";/.test(
        read(join(src, file)),
      );
    // Embed and the app both render the shared components, which load it.
    expect(loads("components/ui.tsx")).toBe(true);
    expect(read(join(src, "embed/main.tsx"))).toContain("../components/ui");
    expect(loads("editor/main.tsx")).toBe(true);
    expect(loads("editor/WikiPlateEditor.tsx")).toBe(true);
  });

  it("themes Plate through its variables and adds no broad override of Plate or Excalidraw", () => {
    expect(tokens).toMatch(/--plate-primary:\s*var\(--accent\)/);
    const selectors = (file: string) =>
      [
        ...read(file)
          .replace(/\/\*[\s\S]*?\*\//g, "")
          .matchAll(/([^{}]+)\{/g),
      ]
        .map((m) => m[1].trim())
        .filter((s) => !s.startsWith("@") && !/^[\d.]+%$/.test(s))
        .flatMap((s) => s.split(","))
        .map((s) => s.trim());
    const risky = (s: string) =>
      /\.excalidraw|\[data-slot|\[class[*^$]=|\.plate-scope\s*\*/.test(s);
    const found = cssFiles(src).flatMap((file) =>
      selectors(file)
        .filter(risky)
        .map((s) => `${file.slice(src.length + 1)}: ${s}`),
    );
    // The only reaches into a third party's DOM: the canvas box (a direct
    // child of our wrapper) and the pre-existing border reset of wiki-plate.css
    // that predates the tokens and is not extended.
    expect(found.sort()).toEqual(
      [
        "diagram/diagram-canvas.css: .diagram-canvas > .excalidraw",
        "editor/wiki-plate.css: .plate-scope *",
        "editor/wiki-plate.css: [data-slot]:not(.wiki-editor *)",
      ].sort(),
    );
  });
});
