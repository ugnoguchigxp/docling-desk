import { useState } from "react";
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { useAsyncScope } from "./async-scope";

afterEach(cleanup);

describe("attempt lifetime", () => {
  it("a newer attempt expires the older one and aborts its signal", () => {
    const { result } = renderHook(() => useAsyncScope("a"));
    const first = result.current.start();
    expect(first()).toBe(true);
    expect(first.signal.aborted).toBe(false);
    const second = result.current.start();
    expect(first()).toBe(false);
    expect(first.signal.aborted).toBe(true);
    expect(second()).toBe(true);
    expect(second.signal.aborted).toBe(false);
  });

  it("changing the scope key expires the running attempt", () => {
    const { result, rerender } = renderHook(({ key }) => useAsyncScope(key), {
      initialProps: { key: "doc-1" },
    });
    const attempt = result.current.start();
    rerender({ key: "doc-1" });
    expect(attempt()).toBe(true);
    rerender({ key: "doc-2" });
    expect(attempt()).toBe(false);
    expect(attempt.signal.aborted).toBe(true);
    expect(result.current.start()()).toBe(true);
  });

  it("unmounting expires the attempt", () => {
    const { result, unmount } = renderHook(() => useAsyncScope("a"));
    const attempt = result.current.start();
    unmount();
    expect(attempt()).toBe(false);
    expect(attempt.signal.aborted).toBe(true);
  });

  it("invalidate ends the attempt without starting another", () => {
    const { result } = renderHook(() => useAsyncScope("a"));
    const attempt = result.current.start();
    act(() => result.current.invalidate());
    expect(attempt()).toBe(false);
  });
});

// A screen that shows the result of the request it sent last.
function Lookup({ pending }: { pending: Array<(value: string) => void> }) {
  const [doc, setDoc] = useState("A");
  const [shown, setShown] = useState("");
  const scope = useAsyncScope(doc);
  async function ask(label: string) {
    const isCurrent = scope.start();
    const value = await new Promise<string>((resolve) =>
      pending.push((v) => resolve(`${label}:${v}`)),
    );
    if (isCurrent()) setShown(value);
  }
  return (
    <>
      <button onClick={() => void ask("first")}>ask-first</button>
      <button onClick={() => void ask("second")}>ask-second</button>
      <button onClick={() => setDoc("B")}>switch</button>
      <output>{shown}</output>
    </>
  );
}

describe("responses arriving late", () => {
  it("keeps the answer to the last question when responses arrive in reverse order", async () => {
    const pending: Array<(value: string) => void> = [];
    render(<Lookup pending={pending} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("ask-first"));
    await user.click(screen.getByText("ask-second"));
    await act(async () => pending[1]("new"));
    expect(screen.getByRole("status")).toHaveTextContent("second:new");
    await act(async () => pending[0]("old"));
    expect(screen.getByRole("status")).toHaveTextContent("second:new");
  });

  it("drops a double click's first result, not the second's", async () => {
    const pending: Array<(value: string) => void> = [];
    render(<Lookup pending={pending} />);
    const user = userEvent.setup();
    await user.dblClick(screen.getByText("ask-first"));
    expect(pending).toHaveLength(2);
    await act(async () => pending[0]("one"));
    expect(screen.getByRole("status")).toHaveTextContent("");
    await act(async () => pending[1]("two"));
    expect(screen.getByRole("status")).toHaveTextContent("first:two");
  });

  it("shows nothing from the previous document after switching", async () => {
    const pending: Array<(value: string) => void> = [];
    render(<Lookup pending={pending} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("ask-first"));
    await user.click(screen.getByText("switch"));
    await act(async () => pending[0]("A's answer"));
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("a result that arrives after unmount changes nothing and throws nothing", async () => {
    const pending: Array<(value: string) => void> = [];
    const { unmount } = render(<Lookup pending={pending} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("ask-first"));
    unmount();
    await act(async () => pending[0]("late"));
    expect(document.body).toHaveTextContent("");
  });
});
