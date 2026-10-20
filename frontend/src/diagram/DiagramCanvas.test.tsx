import { useLayoutEffect } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Props = Record<string, any>;

const state = vi.hoisted(() => ({
  props: null as null | Record<string, any>, // eslint-disable-line @typescript-eslint/no-explicit-any
  restore: vi.fn(),
  serialize: vi.fn(),
  api: {
    addFiles: vi.fn(),
    updateScene: vi.fn(),
    scrollToContent: vi.fn(),
    history: { clear: vi.fn() },
  },
}));
vi.mock("@excalidraw/excalidraw", () => ({
  Excalidraw: (props: Props) => {
    state.props = props;
    // The real component hands out its API in componentDidMount (commit phase).
    useLayoutEffect(() => {
      props.excalidrawAPI?.(state.api);
    }, [props]);
    return (
      <div
        data-testid="excalidraw"
        data-view={String(props.viewModeEnabled)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "z") e.currentTarget.dataset.undo = "1";
        }}
      />
    );
  },
  restore: state.restore,
  serializeAsJSON: state.serialize,
  hashElementsVersion: (elements: Array<{ version: number }>) =>
    elements.reduce((sum, e) => sum + e.version, 0),
  CaptureUpdateAction: { IMMEDIATELY: "IMMEDIATELY", NEVER: "NEVER" },
}));

import DiagramCanvas, { CHANGE_DEBOUNCE_MS, applyScene } from "./DiagramCanvas";

const appState = { viewBackgroundColor: "#fff", gridSize: null };

beforeEach(() => {
  state.restore.mockImplementation(
    (data: { elements: unknown[]; files?: object }) => ({
      elements: data.elements,
      appState: {},
      files: data.files ?? {},
    }),
  );
  state.serialize.mockImplementation((elements: unknown[]) =>
    JSON.stringify({ type: "excalidraw", elements }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

const loaded = () => screen.findByTestId("excalidraw");

test("restores the initial scene once and ignores later initialScene changes", async () => {
  const { rerender } = render(
    <DiagramCanvas
      initialScene={JSON.stringify({ elements: [{ id: "a" }] })}
    />,
  );
  await loaded();
  expect(state.restore).toHaveBeenCalledTimes(1);
  expect(state.props?.initialData.elements).toEqual([{ id: "a" }]);
  rerender(
    <DiagramCanvas
      initialScene={JSON.stringify({ elements: [{ id: "b" }] })}
    />,
  );
  expect(state.restore).toHaveBeenCalledTimes(1);
  expect(state.props?.initialData.elements).toEqual([{ id: "a" }]);
});

test("starts empty without a scene and hides Excalidraw's own file actions", async () => {
  render(<DiagramCanvas initialScene={null} />);
  await loaded();
  expect(state.props?.initialData.elements).toEqual([]);
  expect(state.props?.UIOptions.canvasActions).toMatchObject({
    loadScene: false,
    saveToActiveFile: false,
  });
  expect(state.props?.validateEmbeddable).toBe(false);
});

test("emits serialized local JSON only when the saved scene changes", async () => {
  const onChange = vi.fn();
  render(<DiagramCanvas initialScene={null} onChange={onChange} />);
  await loaded();
  vi.useFakeTimers();
  const els = [{ id: "a", version: 1 }];
  // The first report is immediate: it is the restored initial state.
  act(() => state.props?.onChange(els, appState, {}));
  act(() => state.props?.onChange(els, { ...appState, selected: 1 }, {}));
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(state.serialize).toHaveBeenCalledTimes(1);
  expect(state.serialize).toHaveBeenCalledWith(els, appState, {}, "local");
  act(() => state.props?.onChange([{ id: "a", version: 2 }], appState, {}));
  act(() => vi.advanceTimersByTime(CHANGE_DEBOUNCE_MS));
  expect(onChange).toHaveBeenCalledTimes(2);
});

test("later changes are serialized once input pauses, not on every frame", async () => {
  const onChange = vi.fn();
  render(<DiagramCanvas initialScene={null} onChange={onChange} />);
  await loaded();
  vi.useFakeTimers();
  act(() => state.props?.onChange([{ id: "a", version: 1 }], appState, {}));
  state.serialize.mockClear();
  for (let version = 2; version <= 6; version++) {
    act(() => state.props?.onChange([{ id: "a", version }], appState, {}));
    act(() => vi.advanceTimersByTime(CHANGE_DEBOUNCE_MS - 1));
  }
  expect(state.serialize).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1));
  expect(state.serialize).toHaveBeenCalledTimes(1);
  expect(onChange).toHaveBeenCalledTimes(2);
});

test("a finished stroke, a blur and unmounting send the pending change at once", async () => {
  const onChange = vi.fn();
  const { unmount } = render(
    <DiagramCanvas initialScene={null} onChange={onChange} />,
  );
  const canvas = await loaded();
  vi.useFakeTimers();
  act(() => state.props?.onChange([{ id: "a", version: 1 }], appState, {}));
  act(() => state.props?.onChange([{ id: "a", version: 2 }], appState, {}));
  fireEvent.pointerUp(canvas);
  act(() => vi.advanceTimersByTime(0));
  expect(onChange).toHaveBeenCalledTimes(2);
  act(() => state.props?.onChange([{ id: "a", version: 3 }], appState, {}));
  fireEvent.blur(canvas);
  expect(onChange).toHaveBeenCalledTimes(3);
  act(() => state.props?.onChange([{ id: "a", version: 4 }], appState, {}));
  unmount();
  expect(onChange).toHaveBeenCalledTimes(4);
  expect(vi.getTimerCount()).toBe(0);
});

test("read-only mode shows the scene but never reports changes", async () => {
  const onChange = vi.fn();
  render(<DiagramCanvas initialScene={null} readOnly onChange={onChange} />);
  expect((await loaded()).dataset.view).toBe("true");
  act(() => state.props?.onChange([{ id: "a", version: 1 }], appState, {}));
  expect(onChange).not.toHaveBeenCalled();
});

test("shows an error instead of an empty canvas for unreadable scenes", async () => {
  const onError = vi.fn();
  render(<DiagramCanvas initialScene="{broken" onError={onError} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "図表のデータを読み込めませんでした。",
  );
  expect(screen.queryByTestId("excalidraw")).toBeNull();
  expect(onError).toHaveBeenCalled();
});

test("hands out the imperative API and clears it on unmount", async () => {
  const apiRef = vi.fn();
  const { unmount } = render(
    <DiagramCanvas initialScene={null} apiRef={apiRef} />,
  );
  await loaded();
  expect(apiRef).toHaveBeenCalledWith(state.api);
  unmount();
  expect(apiRef).toHaveBeenLastCalledWith(null);
});

test("Undo/Redo keystrokes reach Excalidraw but not host handlers", async () => {
  const host = vi.fn();
  render(
    <div onKeyDown={host}>
      <DiagramCanvas initialScene={null} />
    </div>,
  );
  const canvas = await loaded();
  fireEvent.keyDown(canvas, { key: "z", ctrlKey: true });
  expect(canvas.dataset.undo).toBe("1");
  fireEvent.keyDown(canvas, { key: "Z", metaKey: true, shiftKey: true });
  fireEvent.keyDown(canvas, { key: "y", ctrlKey: true });
  expect(host).not.toHaveBeenCalled();
  fireEvent.keyDown(canvas, { key: "a", ctrlKey: true });
  expect(host).toHaveBeenCalledTimes(1);
});

test("applyScene replaces the scene as one undo step through the API", async () => {
  await applyScene(
    state.api as never,
    JSON.stringify({
      elements: [{ id: "n" }],
      files: { f: { id: "f", mimeType: "image/png", dataURL: "d" } },
    }),
    { clearHistory: true },
  );
  expect(state.api.addFiles).toHaveBeenCalledWith([
    { id: "f", mimeType: "image/png", dataURL: "d" },
  ]);
  expect(state.api.updateScene).toHaveBeenCalledWith(
    expect.objectContaining({
      elements: [{ id: "n" }],
      captureUpdate: "IMMEDIATELY",
    }),
  );
  expect(state.api.history.clear).toHaveBeenCalled();
});

test("takes a refused image format out of the scene instead of reporting it", async () => {
  const onChange = vi.fn();
  const onNotice = vi.fn();
  render(
    <DiagramCanvas
      initialScene={null}
      onChange={onChange}
      onNotice={onNotice}
    />,
  );
  await loaded();
  const image = { id: "i", type: "image", fileId: "f", version: 1 };
  const keep = { id: "k", type: "rectangle", version: 1 };
  act(() =>
    state.props?.onChange([image, keep], appState, {
      f: { id: "f", mimeType: "image/svg+xml" },
    }),
  );
  expect(onChange).not.toHaveBeenCalled();
  expect(onNotice).toHaveBeenCalledWith(
    expect.stringContaining("PNG、JPEG、WebP、GIF"),
  );
  expect(state.api.updateScene).toHaveBeenCalledWith({
    elements: [{ ...image, isDeleted: true }, keep],
    captureUpdate: "NEVER",
  });
  // Once the image is deleted the leftover file no longer blocks saving.
  state.api.updateScene.mockClear();
  act(() =>
    state.props?.onChange(
      [{ ...image, isDeleted: true, version: 2 }, keep],
      appState,
      {
        f: { id: "f", mimeType: "image/svg+xml" },
      },
    ),
  );
  expect(state.api.updateScene).not.toHaveBeenCalled();
  expect(onChange).toHaveBeenCalledTimes(1);
});

test("accepted image formats pass through untouched", async () => {
  const onChange = vi.fn();
  render(<DiagramCanvas initialScene={null} onChange={onChange} />);
  await loaded();
  act(() =>
    state.props?.onChange(
      [{ id: "i", type: "image", fileId: "f", version: 1 }],
      appState,
      { f: { id: "f", mimeType: "image/webp" } },
    ),
  );
  expect(state.api.updateScene).not.toHaveBeenCalled();
  expect(onChange).toHaveBeenCalledTimes(1);
});
