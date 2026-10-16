import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WikiEditor, newWikiDraft } from "./WikiEditor";

vi.mock("../editor/WikiPlate", () => ({
  WikiPlate: () => <div aria-label="本文ブロック" contentEditable />,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("starts with an empty Plate editor and saves pending keywords with article information", async () => {
  const initial = newWikiDraft();
  const saved = vi.fn();
  const fetcher = vi.fn(
    async (_url, init: RequestInit) =>
      new Response(
        JSON.stringify({
          id: initial.id,
          ...JSON.parse(init.body as string),
          etag: '"saved"',
        }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <WikiEditor
      initial={initial}
      namespaces={[]}
      onSaved={saved}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("本文ブロック")).toHaveAttribute(
    "contenteditable",
    "true",
  );
  expect(screen.getByText("新しい記事 · 未保存")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("記事タイトル"), {
    target: { value: "新しい記事" },
  });
  fireEvent.change(screen.getByLabelText("記事パス"), {
    target: { value: "writing/new.md" },
  });
  fireEvent.change(screen.getByLabelText("概要・副題"), {
    target: { value: "副題" },
  });
  fireEvent.change(screen.getByLabelText("キーワードを追加"), {
    target: { value: "Markdown" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Markdown$/ }));
  fireEvent.change(screen.getByLabelText("Markdownソース"), {
    target: { value: "## 見出し\n\n本文😀" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Wikiに保存" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const payload = JSON.parse(fetcher.mock.calls[0][1].body as string);
  expect(payload.article_info).toMatchObject({
    subtitle: "副題",
    keywords: ["Markdown"],
  });
  expect(payload.body).toBe("## 見出し\n\n本文😀");
  expect(payload).not.toHaveProperty("id");
  expect(screen.getByRole("button", { name: /投稿設定/ })).toBeDisabled();
});

it("retains input and the article identity on failure, then retries without creating another article", async () => {
  const initial = newWikiDraft();
  initial.body = "Original";
  initial.path = "new.md";
  initial.title = "Title";
  let fail = true;
  const fetcher = vi.fn(async (_url, init: RequestInit) => {
    if (fail)
      return new Response(JSON.stringify({ detail: "保存できませんでした" }), {
        status: 503,
      });
    return new Response(
      JSON.stringify({
        ...initial,
        ...JSON.parse(init.body as string),
        etag: '"saved"',
      }),
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const saved = vi.fn();
  render(
    <WikiEditor
      initial={initial}
      namespaces={[]}
      onSaved={saved}
      onClose={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("概要・副題"), {
    target: { value: "失いたくない内容" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Wikiに保存" }));
  await screen.findByText("保存できませんでした");
  expect(screen.getByLabelText("概要・副題")).toHaveValue("失いたくない内容");
  expect(saved).not.toHaveBeenCalled();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Wikiに保存" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(fetcher.mock.calls[1][0]).toBe(fetcher.mock.calls[0][0]);
  expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
});
