import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

const src = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Every module `entry` reaches through relative imports, as absolute paths. */
function reachable(entry: string) {
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(
      /(?:from|import)\s*\(?\s*["'](\.{1,2}\/[^"']+)["']/g,
    )) {
      const target = resolve(dirname(file), m[1]);
      if (/\.(css|svg|png)$/.test(target)) continue;
      for (const candidate of [
        `${target}.ts`,
        `${target}.tsx`,
        join(target, "index.ts"),
        join(target, "index.tsx"),
        target,
      ]) {
        try {
          if (readFileSync(candidate, "utf8") !== undefined) {
            queue.push(candidate);
            break;
          }
        } catch {
          // try the next spelling
        }
      }
    }
  }
  return [...seen];
}

it("keeps the app shell out of the embed entry and the shared document viewer", () => {
  for (const entry of ["embed/main.tsx", "viewer/DocumentViewer.tsx"]) {
    const files = reachable(join(src, entry)).map((f) =>
      f.slice(src.length + 1),
    );
    expect(files.length).toBeGreaterThan(1);
    expect(files.filter((f) => f.startsWith("app/"))).toEqual([]);
    // The Wiki / search code the shell navigates between is not part of it either.
    expect(files.filter((f) => f.startsWith("features/knowledge/"))).toEqual(
      [],
    );
    expect(files).not.toContain("App.tsx");
  }
});
