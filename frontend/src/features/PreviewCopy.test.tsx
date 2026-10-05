import { useCallback, useRef } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { usePreviewCopy } from "./PreviewCopy";
afterEach(cleanup);
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});

function Preview({
  unit = "1",
  hidden = false,
}: {
  unit?: string;
  hidden?: boolean;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const frames = useCallback(() => [frame.current], []);
  const copy = usePreviewCopy("job", unit, frames, `page-${unit}`);
  return (
    <>
      <div hidden={hidden}>
        <iframe ref={frame} title="preview" />
      </div>
      {copy.controls}
      {copy.dialog}
    </>
  );
}
function select(text: string, trusted = true, unitId = "page-1") {
  const frame = screen.getByTitle("preview") as HTMLIFrameElement;
  act(() =>
    window.dispatchEvent(
      new MessageEvent("message", {
        source: trusted ? frame.contentWindow : window,
        data: { type: "docling-text-selection", jobId: "job", unitId, text },
      }),
    ),
  );
}
describe("preview selection copy", () => {
  it("rejects a delayed selection from the previous page in the same frame", () => {
    const view = render(<Preview />);
    select("Previous page");
    view.rerender(<Preview unit="2" />);
    select("Late previous page");
    expect(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    ).toBeDisabled();
    select("Current page", true, "page-2");
    expect(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    ).toBeEnabled();
  });
  it("does not reopen an obsolete fallback after the selection changes", async () => {
    let reject!: (error: Error) => void;
    const pending = new Promise<void>((_resolve, fail) => {
      reject = fail;
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockReturnValue(pending) },
    });
    render(<Preview />);
    select("First selection");
    fireEvent.click(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    );
    select("Second selection");
    await act(async () => {
      reject(new Error("denied"));
      await pending.catch(() => {});
    });
    expect(
      screen.queryByRole("textbox", { name: "コピーする文字" }),
    ).toBeNull();
  });
  it("does not show an old clipboard failure after changing the document unit", async () => {
    let reject!: (error: Error) => void;
    const pending = new Promise<void>((_resolve, fail) => {
      reject = fail;
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockReturnValue(pending) },
    });
    const view = render(<Preview />);
    select("Previous page");
    fireEvent.click(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    );
    view.rerender(<Preview unit="2" />);
    await act(async () => {
      reject(new Error("denied"));
      await pending.catch(() => {});
    });
    expect(
      screen.queryByRole("textbox", { name: "コピーする文字" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    ).toBeDisabled();
  });
  it("ignores selections from a preview hidden by a different tab", () => {
    render(<Preview hidden />);
    select("hidden text");
    expect(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    ).toBeDisabled();
  });
  it("accepts the active frame and copies selected text, then clears on unit change", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const view = render(<Preview />);
    const button = screen.getByRole("button", { name: "選択した文字をコピー" });
    select("untrusted", false);
    expect(button).toBeDisabled();
    select("日本語\nSecond line");
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("日本語\nSecond line"),
    );
    view.rerender(<Preview unit="2" />);
    expect(button).toBeDisabled();
  });
  it("offers selected text for manual copy if the browser denies clipboard access", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<Preview />);
    select("Manual copy");
    fireEvent.click(
      screen.getByRole("button", { name: "選択した文字をコピー" }),
    );
    const field = await screen.findByRole("textbox", {
      name: "コピーする文字",
    });
    expect(field).toHaveValue("Manual copy");
    expect(field).toHaveAttribute("readonly");
  });
});
