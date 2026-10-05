import {
  link,
  mkdir,
  readFile,
  readdir,
  realpath,
  rename,
  writeFile,
  unlink,
} from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const destination = new URL("../../src/docling_desk/resources/static/frontend/", import.meta.url);
const entry = await readFile(new URL("../dist/index.html", import.meta.url));
await mkdir(destination, { recursive: true });
await writeFile(
  new URL("print.html", destination),
  await readFile(new URL("../dist/print.html", import.meta.url)),
);
const embedEntry = await readFile(
  new URL("../dist/embed.html", import.meta.url),
);
// Publish assets before HTML and retain older hashes for already-open viewers.
async function publishAssets(source, target) {
  await mkdir(target, { recursive: true });
  for (const item of await readdir(source, { withFileTypes: true })) {
    const from = new URL(item.name, source),
      to = new URL(item.name, target);
    if (item.isDirectory()) {
      await publishAssets(
        new URL(item.name + "/", source),
        new URL(item.name + "/", target),
      );
      continue;
    }
    const bytes = await readFile(from),
      temporary = new URL(`.asset-${randomUUID()}`, target);
    try {
      await writeFile(temporary, bytes);
      // Publish complete immutable files without replacing an existing inode.
      // Concurrent exports of the same build can safely share the result.
      await link(temporary, to).catch(async (error) => {
        if (error.code !== "EEXIST") throw error;
        if (!(await readFile(to)).equals(bytes))
          throw new Error(`Asset content changed: ${item.name}`);
      });
    } finally {
      await unlink(temporary).catch((error) => {
        if (error.code !== "ENOENT") throw error;
      });
    }
  }
}
await publishAssets(
  new URL("../dist/assets/", import.meta.url),
  new URL("assets/", destination),
);
for (const match of embedEntry
  .toString()
  .matchAll(/(?:src|href)="\/static\/frontend\/(assets\/[^"?#]+)"/g))
  await readFile(new URL(match[1], destination));
const embedNext = new URL(`.embed-${randomUUID()}.html`, destination);
try {
  await writeFile(embedNext, embedEntry);
  await rename(embedNext, new URL("embed.html", destination));
} finally {
  await unlink(embedNext).catch((error) => {if (error.code !== "ENOENT") throw error;});
}
// A concurrent build can replace dist during the copy. Never publish an entry
// whose referenced assets did not reach the destination.
for (const match of entry
  .toString()
  .matchAll(/(?:src|href)="\/static\/frontend\/(assets\/[^"?#]+)"/g))
  await readFile(new URL(match[1], destination));
const dependencyRoot = fileURLToPath(
  new URL("../node_modules/", import.meta.url),
);
const packages = [
  "react",
  "react-dom",
  "@tanstack/react-query",
  "ag-grid-community",
  "ag-grid-react",
];
const licenses = [];
for (const name of packages) {
  const folder = await realpath(join(dependencyRoot, name));
  licenses.push(
    `${name}\n${await readFile(join(folder, name.startsWith("ag-grid-") ? "LICENSE.txt" : "LICENSE"), "utf8")}`,
  );
  const transitive =
    name === "react-dom"
      ? "scheduler"
      : name === "@tanstack/react-query"
        ? "query-core"
        : null;
  if (transitive)
    licenses.push(
      `${transitive}\n${await readFile(join(dirname(folder), transitive, "LICENSE"), "utf8")}`,
    );
}
await writeFile(new URL("licenses.txt", destination), licenses.join("\n\n"));
const next = new URL(`.index-${randomUUID()}.html`, destination);
try {
  await writeFile(next, entry);
  await rename(next, new URL("index.html", destination));
} finally {
  await unlink(next).catch((error) => {
    if (error.code !== "ENOENT") throw error;
  });
}
