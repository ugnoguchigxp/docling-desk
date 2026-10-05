import { useRef } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useWordPreview } from "./Word";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Preview({ visible = true }: { visible?: boolean }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const word = useWordPreview("word-job", true, visible, frame);
  return (
    <>
      <iframe ref={frame} title="Word" />
      {word.controls}
    </>
  );
}
describe("Word display controls", () => {
  it("shares zoom presets and Fit, preserving manual zoom across tabs", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    const view = render(<Preview />);
    const frame = screen.getByTitle("Word") as HTMLIFrameElement;
    Object.defineProperty(frame, "clientWidth", { value: 800 });
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    const zoom = screen.getByRole("combobox", { name: "Wordのズーム倍率" });
    expect(zoom).toBeDisabled();
    act(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frame.contentWindow,
          data: {
            jobId: "word-job",
            type: "docling-document-size",
            width: 600,
          },
        }),
      ),
    );
    await waitFor(() => expect(zoom).toBeEnabled());
    fireEvent.change(zoom, { target: { value: "2" } });
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        { type: "docling-word-zoom", jobId: "word-job", ratio: 2 },
        "*",
      ),
    );
    view.rerender(<Preview visible={false} />);
    expect(
      screen.queryByRole("combobox", { name: "Wordのズーム倍率" }),
    ).toBeNull();
    view.rerender(<Preview />);
    expect(
      screen.getByRole("option", { name: "200%", selected: true }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    expect(
      screen.getByRole("option", { name: "100%", selected: true }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "幅に合わせる" }));
    expect(
      screen.getByRole("button", { name: "幅に合わせる" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("option", { name: "129%", selected: true }),
    ).toBeInTheDocument();
  });
});
