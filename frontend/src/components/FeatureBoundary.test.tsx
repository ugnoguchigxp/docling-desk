import { Suspense } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

afterEach(cleanup);
import { FeatureBoundary, lazyFeature } from "./FeatureBoundary";

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

test("a feature whose module failed to load is fetched again only when its own retry is pressed", async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(
      new Error("Failed to fetch dynamically imported module"),
    )
    .mockResolvedValue({
      default: ({ name }: { name: string }) => <p>{name}を表示</p>,
    });
  const Feature = lazyFeature(load);
  render(
    <>
      <button>周囲の操作</button>
      <FeatureBoundary
        onRetry={Feature.reset}
        fallback={(retry) => (
          <>
            <p role="alert">読み込めませんでした。</p>
            <button onClick={retry}>もう一度読み込む</button>
          </>
        )}
      >
        <Suspense fallback={<p role="status">読み込み中</p>}>
          <Feature name="表" />
        </Suspense>
      </FeatureBoundary>
    </>,
    { onCaughtError: () => {} },
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("読み込めません");
  expect(screen.getByRole("button", { name: "周囲の操作" })).toBeEnabled();
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "もう一度読み込む" }));
  expect(await screen.findByText("表を表示")).toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).toBeNull();
});
