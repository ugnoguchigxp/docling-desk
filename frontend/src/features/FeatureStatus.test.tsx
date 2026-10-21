import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeatureStatus } from "./FeatureStatus";

const feature = (over: Record<string, unknown>) => ({
  id: "x",
  label: "機能",
  description: "説明",
  optional: false,
  depends_on: [],
  status: "available",
  reasons: [],
  recovery: [],
  notes: [],
  prerequisites: [],
  ...over,
});
let body: unknown;
let status = 200;
const fetcher = vi.fn<typeof fetch>(
  async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
);

beforeEach(() => {
  body = { version: 1, platform: "darwin", features: [] };
  status = 200;
  fetcher.mockClear();
  vi.stubGlobal("fetch", fetcher);
  // jsdom has no modal dialog; open means the attribute is set.
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function show() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FeatureStatus />
    </QueryClientProvider>,
  );
}
const item = (id: string) =>
  document.querySelector<HTMLElement>(`[data-feature="${id}"]`)!;

it("asks the server only when it opens and when asked again, never on a timer or focus", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  body = { features: [feature({ id: "a", label: "閲覧" })] };
  show();
  expect(fetcher).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  expect(
    await screen.findByText("必要な機能はすべて利用できます。"),
  ).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0][0])).toBe("/api/diagnostics/features");
  await vi.advanceTimersByTimeAsync(10 * 60_000);
  fireEvent.focus(window);
  fireEvent(window, new Event("online"));
  document.dispatchEvent(new Event("visibilitychange"));
  expect(fetcher).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "もう一度確認" }));
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  // Closing and opening again is a new look, not a cached one.
  await user.click(screen.getAllByRole("button", { name: "閉じる" })[1]);
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
});

it("shows an optional feature that is not configured as guidance, not as a problem", async () => {
  const user = userEvent.setup();
  body = {
    features: [
      feature({ id: "ok", label: "閲覧" }),
      feature({
        id: "semantic",
        label: "意味検索",
        optional: true,
        status: "not_configured",
        recovery: ["埋め込みの接続情報を設定してください。"],
      }),
    ],
  };
  show();
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  const row = within(
    await vi.waitFor(() => {
      const el = item("semantic");
      if (!el) throw new Error("not yet");
      return el;
    }),
  );
  const state = row.getByText("未設定です（任意）");
  expect(state).toHaveAttribute("data-kind", "setup");
  expect(state).toHaveAttribute("role", "status");
  expect(state).toHaveAttribute("data-need", "guidance");
  expect(row.getByText("埋め込みの接続情報を設定してください。")).toBeTruthy();
  // Nothing on the screen is an alert, and nothing needs doing.
  expect(screen.queryByRole("alert")).toBeNull();
  const summary = screen.getByText("必要な機能はすべて利用できます。");
  expect(summary).not.toHaveAttribute("data-kind", "error");
  expect(summary).toHaveTextContent(/任意の機能のうち1件は未設定です/);
});

it("asks for action on a required gap and alerts only on a temporary failure", async () => {
  const user = userEvent.setup();
  body = {
    features: [
      feature({
        id: "conversion",
        label: "変換",
        status: "not_configured",
        recovery: ["モデルを取得してください。"],
      }),
      feature({
        id: "blob",
        label: "Blob",
        status: "temporary_failure",
        recovery: ["同期を再実行してください。"],
      }),
    ],
  };
  show();
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  await vi.waitFor(() => expect(item("conversion")).toBeTruthy());
  const required = within(item("conversion")).getByText(
    "必要な設定が不足しています",
  );
  expect(required).toHaveAttribute("data-need", "action");
  expect(required).toHaveAttribute("role", "status");
  const failing = within(item("blob")).getByText("一時的に利用できません");
  expect(failing).toHaveAttribute("role", "alert");
  expect(screen.getByText("対応が必要な機能が2件あります。")).toHaveAttribute(
    "role",
    "alert",
  );
});

it("does not let an unrelated optional provider make an available feature look unusable", async () => {
  const user = userEvent.setup();
  body = {
    features: [
      feature({
        id: "conversion",
        label: "変換",
        status: "available",
        notes: ["azure_ocr_not_configured"],
        prerequisites: [
          {
            id: "models",
            label: "抽出モデル",
            required: true,
            status: "ok",
            code: null,
            recovery: null,
          },
          {
            id: "azure_ocr",
            label: "Azure OCR",
            required: false,
            status: "not_configured",
            code: "azure_ocr_not_configured",
            recovery: "Azureの設定を追加してください。",
          },
        ],
      }),
    ],
  };
  show();
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  await vi.waitFor(() => expect(item("conversion")).toBeTruthy());
  const row = within(item("conversion"));
  const state = row.getByText("利用できます");
  expect(state).not.toHaveAttribute("data-kind");
  expect(row.getByText(/Azure OCRは未設定です（任意）/)).toHaveTextContent(
    "この機能の利用には影響しません",
  );
  // The optional provider's own recovery is not offered as a step to take.
  expect(screen.queryByText("Azureの設定を追加してください。")).toBeNull();
  expect(screen.getByText("必要な機能はすべて利用できます。")).toBeTruthy();
});

it("shows only the labels, descriptions and steps, never other fields of the response", async () => {
  const user = userEvent.setup();
  body = {
    features: [
      feature({
        id: "blob",
        status: "temporary_failure",
        endpoint: "https://secret.example/key=abc",
        path: "/Users/someone/private",
        api_key: "sk-secret",
        prerequisites: [
          {
            id: "p",
            label: "接続",
            required: true,
            status: "temporary_failure",
            token: "tok-123",
            recovery: null,
          },
        ],
      }),
    ],
  };
  show();
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  await vi.waitFor(() => expect(item("blob")).toBeTruthy());
  const shown = document.body.textContent ?? "";
  for (const secret of [
    "secret.example",
    "/Users/someone",
    "sk-secret",
    "tok-123",
  ])
    expect(shown).not.toContain(secret);
});

it("reports a failed check with a way to try again, and a malformed answer as a failure", async () => {
  const user = userEvent.setup();
  status = 500;
  body = { detail: "診断できません" };
  show();
  await user.click(screen.getByRole("button", { name: "機能の利用状態" }));
  const failed = await screen.findByText("診断できません");
  expect(failed).toHaveAttribute("role", "alert");
  status = 200;
  body = { nothing: true };
  await user.click(screen.getAllByRole("button", { name: "もう一度確認" })[0]);
  expect(
    await screen.findByText("診断結果を読み込めませんでした。"),
  ).toHaveAttribute("role", "alert");
});
