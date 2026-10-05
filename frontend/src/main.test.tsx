import { act } from "@testing-library/react";
import { expect, test, vi } from "vitest";

test("mounts the application on the root element", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ folders: [], jobs: [] }))),
  );
  await act(async () => {
    await import("./main");
  });
  expect(document.getElementById("root")?.innerHTML.length).toBeGreaterThan(0);
});
