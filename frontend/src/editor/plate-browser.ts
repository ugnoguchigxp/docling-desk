import { Buffer } from "buffer";
import { withBase } from "../lib/base";
// The standard Word exporter uses Node's byte API, provided locally in browsers.
if (!("Buffer" in globalThis))
  Object.defineProperty(globalThis, "Buffer", { value: Buffer });
// Excalidraw's distributed fonts are served with the app instead of its public CDN.
Object.assign(window, {
  EXCALIDRAW_ASSET_PATH: import.meta.env.DEV
    ? "/node_modules/@excalidraw/excalidraw/dist/dev/"
    : withBase("/static/frontend/excalidraw/"),
});
