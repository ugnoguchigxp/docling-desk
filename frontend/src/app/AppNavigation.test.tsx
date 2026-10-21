import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppNavigation } from "./AppNavigation";

describe("AppNavigation", () => {
  it("marks the active mode and reports mode, search and RAG choices", async () => {
    const onMode = vi.fn();
    const onSearch = vi.fn();
    const onRag = vi.fn();
    const user = userEvent.setup();
    const view = render(
      <AppNavigation
        mode="library"
        onMode={onMode}
        onSearch={onSearch}
        onRag={onRag}
      />,
    );
    expect(screen.getByRole("button", { name: "資料一覧" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: "Wiki" })).not.toHaveAttribute(
      "aria-current",
    );
    await user.click(screen.getByRole("button", { name: "Wiki" }));
    expect(onMode).toHaveBeenCalledWith("wiki");
    await user.click(screen.getByRole("button", { name: "資料一覧" }));
    expect(onMode).toHaveBeenCalledWith("library");
    view.rerender(
      <AppNavigation mode="wiki" onMode={onMode} onSearch={onSearch} />,
    );
    expect(screen.getByRole("button", { name: "Wiki" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("button", { name: "資料一覧" }),
    ).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("button", { name: "RAG" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "本文を検索" }));
    expect(onSearch).toHaveBeenCalledOnce();
    view.rerender(
      <AppNavigation
        mode="wiki"
        onMode={onMode}
        onSearch={onSearch}
        onRag={onRag}
      />,
    );
    await user.click(screen.getByRole("button", { name: "RAG" }));
    expect(onRag).toHaveBeenCalledOnce();
  });

  it("offers a return control only when one is given, and runs it once", async () => {
    const onResume = vi.fn();
    const user = userEvent.setup();
    const view = render(
      <AppNavigation mode="wiki" onMode={vi.fn()} onSearch={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "検索結果へ戻る" })).toBeNull();
    view.rerender(
      <AppNavigation
        mode="wiki"
        onMode={vi.fn()}
        onSearch={vi.fn()}
        resume={{ label: "検索結果へ戻る", onResume }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "検索結果へ戻る" }));
    expect(onResume).toHaveBeenCalledOnce();
  });
});
