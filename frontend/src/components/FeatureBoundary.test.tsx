import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { FeatureBoundary } from "./FeatureBoundary";

test("a failed optional feature leaves the surrounding document controls available", () => {
  function Broken(): never {
    throw new Error("chunk unavailable");
  }
  render(
    <>
      <button>原本プレビュー</button>
      <FeatureBoundary
        fallback={<p role="alert">表を再読み込みしてください。</p>}
      >
        <Broken />
      </FeatureBoundary>
    </>,
    { onCaughtError: () => {} },
  );
  expect(screen.getByRole("alert")).toHaveTextContent("再読み込み");
  expect(screen.getByRole("button", { name: "原本プレビュー" })).toBeEnabled();
});
