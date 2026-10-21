import {
  QueryClient,
  QueryObserver,
  focusManager,
  onlineManager,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BACKOFF_MS,
  WATCH_MS,
  libraryInterval,
  pollInterval,
  searchInterval,
} from "./polling";
import type { Library } from "./types";

const library = (...states: string[]): Library =>
  ({
    folders: [],
    jobs: states.map((state, i) => ({ id: `j${i}`, state })),
  }) as unknown as Library;

describe("pollInterval", () => {
  const rule = {
    busy: 1500,
    isBusy: (n: number) => n > 0,
    idle: false as const,
  };
  it("polls fast only while the server is busy", () => {
    expect(pollInterval({ data: 1 }, rule)).toBe(1500);
    expect(pollInterval({ data: 0 }, rule)).toBe(false);
    expect(pollInterval({}, rule)).toBe(false);
  });
  it("stretches a polling interval after a failure and never starts one", () => {
    expect(pollInterval({ data: 1, error: new Error("x") }, rule)).toBe(
      BACKOFF_MS,
    );
    expect(pollInterval({ data: 0, error: new Error("x") }, rule)).toBe(false);
    expect(
      pollInterval(
        { data: 0, error: new Error("x") },
        { ...rule, idle: WATCH_MS },
      ),
    ).toBe(BACKOFF_MS);
  });
});

describe("what each query waits for", () => {
  it("the list is fast while a document is processed, in any mode", () => {
    for (const state of ["queued", "running"])
      for (const mode of ["library", "wiki"])
        expect(libraryInterval({ data: library("success", state) }, mode)).toBe(
          2000,
        );
  });
  it("an idle list is watched on the list screen only", () => {
    const data = library("success", "partial", "failed");
    expect(libraryInterval({ data }, "library")).toBe(WATCH_MS);
    expect(libraryInterval({ data }, "wiki")).toBe(false);
  });
  it("a search is followed until it is final, then only until its sources change", () => {
    const task = (state: string, stale = false) => ({
      data: { state, output: { stale } },
    });
    expect(searchInterval(task("queued"))).toBe(2000);
    expect(searchInterval(task("running"))).toBe(2000);
    expect(searchInterval(task("completed"))).toBe(WATCH_MS);
    expect(searchInterval(task("completed", true))).toBe(false);
    expect(searchInterval(task("failed"))).toBe(false);
    expect(searchInterval(task("cancelled"))).toBe(false);
    expect(searchInterval({})).toBe(false);
    // A task without output yet is not an outdated one.
    expect(searchInterval({ data: { state: "completed" } })).toBe(WATCH_MS);
  });
});

// The same observer a screen would hold, against a fake server and clock.
describe("requests over time", () => {
  let server: { data: Library; fail: boolean };
  let requests: number[];
  let client: QueryClient;
  beforeEach(() => {
    vi.useFakeTimers();
    server = { data: library("success"), fail: false };
    requests = [];
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 1000 } },
    });
    client.mount(); // what QueryClientProvider does: online/focus listeners
  });
  afterEach(() => {
    client.unmount();
    client.clear();
    focusManager.setFocused(undefined);
    onlineManager.setOnline(true);
    vi.useRealTimers();
  });
  function watching(
    interval: (state: { data?: Library; error?: unknown }) => number | false,
  ) {
    const started = Date.now();
    const observer = new QueryObserver<Library>(client, {
      queryKey: ["library"],
      queryFn: async () => {
        requests.push(Date.now() - started);
        if (server.fail) throw new Error("down");
        return server.data;
      },
      refetchInterval: (q) => interval(q.state),
    });
    return { observer, stop: observer.subscribe(() => {}) };
  }
  const settle = (ms: number) => vi.advanceTimersByTimeAsync(ms);

  it("asks at least 50% less often over 60 seconds with nothing to wait for", async () => {
    const previous = watching(() => 2000); // the previous fixed policy
    await settle(60_000);
    const before = requests.length;
    previous.stop();
    client.clear();
    requests = [];
    watching((state) => libraryInterval(state, "library"));
    await settle(60_000);
    expect(before).toBeGreaterThanOrEqual(30);
    expect(requests.length).toBeLessThanOrEqual(before / 2);
  });

  it("keeps the old two-second notice delay while a document is processed", async () => {
    server.data = library("running");
    watching((state) => libraryInterval(state, "library"));
    await settle(60_000);
    expect(requests.length).toBeGreaterThanOrEqual(30);
    // Finishing is noticed within one interval, then the cadence relaxes.
    server.data = library("success");
    requests = [];
    await settle(2100);
    expect(requests).toHaveLength(1);
    requests = [];
    await settle(60_000);
    expect(requests.length).toBeLessThanOrEqual(13);
  });

  it("notices a change made elsewhere within 10 seconds", async () => {
    const { observer } = watching((state) => libraryInterval(state, "library"));
    await settle(500);
    server.data = library("success", "success");
    let seen: number | null = null;
    for (let t = 0; t < 10_000 && seen === null; t += 250) {
      await settle(250);
      if (observer.getCurrentResult().data?.jobs.length === 2) seen = t + 250;
    }
    expect(seen).not.toBeNull();
    expect(seen!).toBeLessThanOrEqual(WATCH_MS + 250);
  });

  it("makes no requests in a hidden tab and catches up when it is shown", async () => {
    watching((state) => libraryInterval(state, "library"));
    await settle(500);
    focusManager.setFocused(false);
    requests = [];
    await settle(60_000);
    expect(requests).toHaveLength(0);
    focusManager.setFocused(true);
    await settle(WATCH_MS + 100);
    expect(requests.length).toBeGreaterThanOrEqual(1);
  });

  it("does not poll the list behind another mode, and resumes when asked", async () => {
    let mode = "wiki";
    watching((state) => libraryInterval(state, mode));
    await settle(60_000);
    expect(requests).toHaveLength(1);
    mode = "library";
    await client.refetchQueries({ queryKey: ["library"] });
    await settle(WATCH_MS + 100);
    expect(requests.length).toBeGreaterThanOrEqual(3);
  });

  it("backs off after a failure, then returns to the normal cadence", async () => {
    server.data = library("running");
    watching((state) => libraryInterval(state, "library"));
    await settle(1000);
    server.fail = true;
    requests = [];
    await settle(60_000);
    // Without back-off this would be 30; the first request after the failure
    // is already the stretched one.
    expect(requests.length).toBeLessThanOrEqual(7);
    expect(requests.length).toBeGreaterThanOrEqual(4);
    server.fail = false;
    requests = [];
    await settle(BACKOFF_MS * 2);
    expect(requests.length).toBeGreaterThanOrEqual(2);
  });

  it("asks again at once when the connection comes back", async () => {
    watching((state) => libraryInterval(state, "wiki"));
    await settle(2000);
    requests = [];
    onlineManager.setOnline(false);
    await settle(30_000);
    onlineManager.setOnline(true);
    await settle(10);
    expect(requests).toHaveLength(1);
  });
});
