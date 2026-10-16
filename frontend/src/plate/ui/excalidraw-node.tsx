"use client";

import * as React from "react";
import type { TExcalidrawElement } from "@platejs/excalidraw";
import type { BinaryFiles, AppState } from "@excalidraw/excalidraw/types";
import type { ExcalidrawElement as DrawingElement } from "@excalidraw/excalidraw/element/types";
import {
  PlateElement,
  useReadOnly,
  type PlateElementProps,
} from "platejs/react";
import { cn } from "@/plate/lib/utils";
import "@excalidraw/excalidraw/index.css";

type DrawingData = {
  elements?: readonly DrawingElement[];
  state?: Partial<AppState> | null;
  files?: BinaryFiles;
};

export function ExcalidrawElement(
  props: PlateElementProps<TExcalidrawElement>,
) {
  const readOnly = useReadOnly();
  const [drawing, setDrawing] =
    React.useState<typeof import("@excalidraw/excalidraw")>();
  const lastSaved = React.useRef("");
  const [initialData] = React.useState(() => {
    const data = props.element.data as DrawingData | undefined;
    return {
      elements: data?.elements ?? [],
      appState: data?.state,
      files: data?.files,
    };
  });
  React.useEffect(() => {
    let stopped = false;
    void import("@excalidraw/excalidraw").then((module) => {
      if (!stopped) setDrawing(module);
    });
    return () => {
      stopped = true;
    };
  }, []);
  const Canvas = drawing?.Excalidraw;
  const restored = React.useMemo(
    () => drawing?.restore(initialData, null, null),
    [drawing, initialData],
  );
  return (
    <PlateElement {...props}>
      <div contentEditable={false}>
        <div
          className={cn(
            "mx-auto aspect-video h-[600px] w-[min(100%,600px)] overflow-hidden rounded-sm border",
          )}
        >
          {Canvas && (
            <Canvas
              initialData={restored}
              viewModeEnabled={readOnly}
              onChange={(elements, state, files) => {
                if (readOnly) return;
                // Use Excalidraw's JSON contract: transient Maps and UI state cannot be restored from JSON.
                const serialized = drawing.serializeAsJSON(
                  elements,
                  state,
                  files,
                  "local",
                );
                if (serialized === lastSaved.current) return;
                lastSaved.current = serialized;
                const saved = JSON.parse(serialized);
                const at = props.editor.api.findPath(props.element);
                if (at)
                  props.editor.tf.setNodes(
                    {
                      data: {
                        elements: saved.elements,
                        state: saved.appState,
                        files: saved.files,
                      },
                    },
                    { at },
                  );
              }}
            />
          )}
        </div>
      </div>
      {props.children}
    </PlateElement>
  );
}
