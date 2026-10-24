import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppErrorBoundary } from "./AppErrorBoundary";

afterEach(cleanup);

test("a render exception shows a Japanese fallback with a reload button and is logged", () => {
  const logged = vi.spyOn(console, "error").mockImplementation(() => {});
  const reload = vi.fn();
  function Broken(): never {
    throw new Error("render failed");
  }
  render(
    <AppErrorBoundary reload={reload}>
      <Broken />
    </AppErrorBoundary>,
    { onCaughtError: () => {} },
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "画面を表示できませんでした",
  );
  fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
  expect(reload).toHaveBeenCalledOnce();
  expect(
    logged.mock.calls.some((call) =>
      call.some(
        (arg) => arg instanceof Error && arg.message === "render failed",
      ),
    ),
  ).toBe(true);
  logged.mockRestore();
});

test("renders its children when nothing fails", () => {
  render(
    <AppErrorBoundary>
      <p>通常表示</p>
    </AppErrorBoundary>,
  );
  expect(screen.getByText("通常表示")).toBeInTheDocument();
});
