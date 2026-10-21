export const DIAGRAM_EXTENSION = ".excalidraw";
export const DEFAULT_DIAGRAM_NAME = "新しい図表";
/** The file name the server stores: the input with the extension ensured. */
export function diagramFilename(input: string) {
  const trimmed = input.trim() || DEFAULT_DIAGRAM_NAME;
  return trimmed.toLowerCase().endsWith(DIAGRAM_EXTENSION)
    ? trimmed
    : trimmed + DIAGRAM_EXTENSION;
}
/** The minimal standard empty Excalidraw scene (the server stores the bytes as sent). */
export function emptyScene() {
  return {
    type: "excalidraw",
    version: 2,
    source: window.location.origin,
    elements: [],
    appState: { gridSize: null, viewBackgroundColor: "#ffffff" },
    files: {},
  };
}
