import * as React from "react";
import type {
  BinaryFiles,
  ExcalidrawImperativeAPI,
} from "@excalidraw/excalidraw/types";
import { ALLOWED_IMAGE_MIME, ensureAssetPath } from "./assets";
import { object } from "../lib/value";
import "@excalidraw/excalidraw/index.css";
import "./diagram-canvas.css";

type Excalidraw = typeof import("@excalidraw/excalidraw");

export interface DiagramCanvasProps {
  /**
   * Scene JSON at open time. Read once: Excalidraw evaluates its initial data
   * only on mount, so later changes go through {@link applyScene} (or a new
   * `key` on this component).
   */
  initialScene: string | null;
  readOnly?: boolean;
  /** Called with `serializeAsJSON(..., "local")` output when the scene changes. */
  onChange?: (scene: string) => void;
  apiRef?: (api: ExcalidrawImperativeAPI | null) => void;
  onError?: (message: string) => void;
  /** Non-fatal messages, e.g. an image that was refused. */
  onNotice?: (message: string) => void;
}

export const CHANGE_DEBOUNCE_MS = 300;

export const IMAGE_REFUSED =
  "この形式の画像は図表に挿入できません。PNG、JPEG、WebP、GIFを使用してください。";

export interface SceneData {
  elements: ReturnType<Excalidraw["restore"]>["elements"];
  appState: ReturnType<Excalidraw["restore"]>["appState"];
  files: BinaryFiles;
}

/** Parse scene JSON and restore it to what the canvas accepts. */
export function loadScene(
  excalidraw: Pick<Excalidraw, "restore">,
  scene: string | null,
): SceneData {
  let parsed: unknown = {};
  if (scene) {
    try {
      parsed = JSON.parse(scene);
    } catch {
      throw new Error("図表のデータを読み込めませんでした。");
    }
    if (!object(parsed))
      throw new Error("図表のデータを読み込めませんでした。");
  }
  const data = parsed as Record<string, unknown>;
  const restored = excalidraw.restore(
    {
      elements: Array.isArray(data.elements) ? data.elements : [],
      appState: object(data.appState) ? data.appState : undefined,
      files: object(data.files) ? (data.files as BinaryFiles) : undefined,
    },
    null,
    null,
  );
  return {
    elements: restored.elements,
    appState: restored.appState,
    files: restored.files ?? {},
  };
}

/**
 * Replace the scene of a mounted canvas through its imperative API, as one
 * undo step. Use this (not a new `initialScene`) for changes after mount.
 */
export async function applyScene(
  api: ExcalidrawImperativeAPI,
  scene: string,
  options: { clearHistory?: boolean } = {},
): Promise<void> {
  const excalidraw = await import("@excalidraw/excalidraw");
  const next = loadScene(excalidraw, scene);
  const files = Object.values(next.files);
  if (files.length) api.addFiles(files);
  api.updateScene({
    elements: next.elements,
    appState: next.appState,
    captureUpdate: excalidraw.CaptureUpdateAction.IMMEDIATELY,
  });
  if (options.clearHistory) api.history.clear();
  api.scrollToContent(undefined, { fitToContent: true });
}

const undoKey = (event: React.KeyboardEvent) =>
  (event.metaKey || event.ctrlKey) &&
  !event.altKey &&
  (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y");

export function DiagramCanvas({
  initialScene,
  readOnly = false,
  onChange,
  apiRef,
  onError,
  onNotice,
}: DiagramCanvasProps) {
  const [excalidraw, setExcalidraw] = React.useState<Excalidraw>();
  const [failure, setFailure] = React.useState("");
  const first = React.useRef(initialScene);
  const callbacks = React.useRef({ onChange, apiRef, onError, onNotice });
  callbacks.current = { onChange, apiRef, onError, onNotice };
  const apiHandle = React.useRef<ExcalidrawImperativeAPI | null>(null);
  // Serializing a large scene on every frame is costly: after the first
  // report, changes are sent once input pauses (or at pointer-up/blur/unmount).
  const emitted = React.useRef({ key: "", scene: "", first: true });
  const queued = React.useRef<{ run: (() => void) | null; timer?: number }>({
    run: null,
  });
  const flush = React.useCallback(() => {
    window.clearTimeout(queued.current.timer);
    const run = queued.current.run;
    queued.current.run = null;
    run?.();
  }, []);
  React.useLayoutEffect(() => flush, [flush]);

  React.useEffect(() => {
    let stopped = false;
    ensureAssetPath();
    void import("@excalidraw/excalidraw").then(
      (module) => {
        if (!stopped) setExcalidraw(module);
      },
      () => {
        if (stopped) return;
        const message = "図表エディターを読み込めませんでした。";
        setFailure(message);
        callbacks.current.onError?.(message);
      },
    );
    return () => {
      stopped = true;
    };
  }, []);
  React.useEffect(
    () => () => {
      callbacks.current.apiRef?.(null);
    },
    [],
  );

  const prepared = React.useMemo(() => {
    if (!excalidraw) return null;
    try {
      return { data: loadScene(excalidraw, first.current), error: "" };
    } catch (error) {
      return {
        data: null,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }, [excalidraw]);
  const error = failure || prepared?.error || "";
  React.useEffect(() => {
    if (prepared?.error) callbacks.current.onError?.(prepared.error);
  }, [prepared]);

  const initialData = React.useMemo(
    () => (prepared?.data ? { ...prepared.data, scrollToContent: true } : null),
    [prepared],
  );
  const handleChange = React.useCallback(
    (
      elements: Parameters<
        NonNullable<React.ComponentProps<Excalidraw["Excalidraw"]>["onChange"]>
      >[0],
      state: Parameters<
        NonNullable<React.ComponentProps<Excalidraw["Excalidraw"]>["onChange"]>
      >[1],
      files: BinaryFiles,
    ) => {
      if (readOnly || !excalidraw) return;
      // The server stores only PNG/JPEG/WebP/GIF images (not SVG): take a
      // refused image out of the scene instead of failing the later save.
      const refused = new Set(
        Object.values(files)
          .filter((file) => !ALLOWED_IMAGE_MIME.includes(file.mimeType))
          .map((file) => file.id),
      );
      const live = (element: (typeof elements)[number]) =>
        element.type === "image" &&
        !element.isDeleted &&
        !!element.fileId &&
        refused.has(element.fileId);
      if (elements.some(live)) {
        apiHandle.current?.updateScene({
          elements: elements.map((element) =>
            live(element) ? { ...element, isDeleted: true } : element,
          ),
          captureUpdate: excalidraw.CaptureUpdateAction.NEVER,
        });
        callbacks.current.onNotice?.(IMAGE_REFUSED);
        return;
      }
      // Excalidraw reports pointer moves and selections too; look at the
      // saved parts only (cheap) before serializing anything.
      const key = [
        excalidraw.hashElementsVersion(elements),
        Object.keys(files).join(","),
        state.viewBackgroundColor,
        state.gridSize,
      ].join("|");
      if (key === emitted.current.key) return;
      emitted.current.key = key;
      queued.current.run = () => {
        const scene = excalidraw.serializeAsJSON(
          elements,
          state,
          files,
          "local",
        );
        if (scene === emitted.current.scene && !emitted.current.first) return;
        emitted.current.first = false;
        emitted.current.scene = scene;
        callbacks.current.onChange?.(scene);
      };
      window.clearTimeout(queued.current.timer);
      if (emitted.current.first) flush();
      else queued.current.timer = window.setTimeout(flush, CHANGE_DEBOUNCE_MS);
    },
    [excalidraw, readOnly, flush],
  );

  const Canvas = excalidraw?.Excalidraw;
  return (
    <div
      className="diagram-canvas"
      // Excalidraw handles Undo/Redo itself; keep the keystroke from also
      // reaching a host editor's (Plate, page) history handling.
      onKeyDown={(event) => {
        if (undoKey(event)) event.stopPropagation();
      }}
      // Excalidraw reports the finished stroke after this event.
      onPointerUp={() => window.setTimeout(flush, 0)}
      onBlur={flush}
    >
      {error ? (
        <p role="alert" className="diagram-canvas-error">
          {error}
        </p>
      ) : (
        Canvas &&
        initialData && (
          <Canvas
            initialData={initialData}
            viewModeEnabled={readOnly}
            langCode="ja-JP"
            validateEmbeddable={false}
            UIOptions={{
              canvasActions: {
                loadScene: false,
                saveToActiveFile: false,
                export: false,
                saveAsImage: false,
              },
            }}
            excalidrawAPI={(api) => {
              apiHandle.current = api;
              callbacks.current.apiRef?.(api);
            }}
            onChange={handleChange}
          />
        )
      )}
    </div>
  );
}

export default DiagramCanvas;
