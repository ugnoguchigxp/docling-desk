import { access, cp, mkdir, rm, writeFile } from "node:fs/promises";
const destination = new URL(
  "../../.cache/frontend-migration-source/data/",
  import.meta.url,
);
const synthetic =
  process.env.UI_SYNTHETIC === "1" || process.env.DOCLING_SYNTHETIC_FIXTURE === "1";
if (synthetic) {
  await rm(destination, { recursive: true, force: true });
  await mkdir(destination, { recursive: true });
  await writeFile(new URL("library.json", destination), '{"folders":{},"files":{}}\n');
} else {
  try {
    await access(destination);
  } catch {
    try {
      await access(new URL("../../data/library.json", import.meta.url));
      await mkdir(new URL("../../.cache/frontend-migration-source/", import.meta.url), {
        recursive: true,
      });
      await cp(new URL("../../data/", import.meta.url), destination, { recursive: true });
    } catch {
      await mkdir(destination, { recursive: true });
      await writeFile(
        new URL("library.json", destination),
        '{"folders":{},"files":{}}\n',
      );
    }
  }
}
