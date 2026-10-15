import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Editor } from "./main";

vi.mock("./RawEditor", () => ({
  RawEditor: () => <div>rich editor</div>,
  sourceEdit: (_original: string, next: string) => next,
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps input entered after a lost save while replaying the original key", async () => {
  let draft = {
    draft_id: "draft",
    source_id: "source",
    base_revision: null,
    base_etag: null,
    body: "original",
    metadata: {
      title: "Title",
      collection_id: "docs",
      source_kind: "wiki",
      language: "ja",
    },
    etag: '"draft:1"',
    sha256: "hash",
    version: 1,
    state: "draft",
    attachments: [],
  };
  const writes: Record<string, unknown>[] = [];
  let lost = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      let result: unknown;
      if (url === "/editor/challenges")
        result = { challenge: "challenge", code: "CODE" };
      else if (url === "/editor/challenges/challenge")
        result = { session: "session" };
      else if (url === "/editor/session/session")
        result = { draft_id: "draft", scope: { collection_id: "docs" } };
      else {
        const call = JSON.parse(options!.body as string);
        if (call.name === "get_document_draft") result = draft;
        else if (call.name === "update_document_draft") {
          writes.push(call.arguments);
          if (!lost) {
            draft = {
              ...draft,
              body: call.arguments.body,
              version: 2,
              etag: '"draft:2"',
            };
            lost = true;
            throw new TypeError("response lost");
          }
          result = draft;
        } else throw new Error("unexpected tool " + call.name);
      }
      return new Response(JSON.stringify(result), { status: 200 });
    }),
  );
  render(<Editor />);
  await screen.findByText("rich editor");
  fireEvent.click(screen.getByRole("button", { name: "ソース編集" }));
  const source = screen.getByRole("textbox", { name: "Markdownソース" });
  fireEvent.change(source, { target: { value: "first edit" } });
  fireEvent.click(screen.getByRole("button", { name: "下書きを保存" }));
  await screen.findByText(/response lost/);
  fireEvent.change(source, {
    target: { value: "first edit plus newer input" },
  });
  expect(screen.getByRole("button", { name: "保存版を確定" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "同じ操作で再試行" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1]).toEqual(writes[0]);
  expect(source).toHaveValue("first edit plus newer input");
  expect(screen.getByRole("status")).toHaveTextContent("未保存の変更あり");
});
