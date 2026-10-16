import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { UploadActivity, useUploadFile } from "./use-upload-file";

vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

class UploadRequest {
  url = "";
  body?: FormData;
  status = 0;
  responseText = "";
  upload = {
    onprogress:
      vi.fn<
        (event: {
          lengthComputable: boolean;
          loaded: number;
          total: number;
        }) => void
      >(),
  };
  onload = () => {};
  onerror = () => {};
  open(_method: string, url: string) {
    this.url = url;
  }
  send(body: FormData) {
    this.body = body;
    requests.push(this);
  }
  respond(status: number, value: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(value);
    this.onload();
  }
}
const requests: UploadRequest[] = [];
const clients: QueryClient[] = [];
const reference = `/api/wiki/images/${"a".repeat(64)}.png`;
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  requests.length = 0;
  vi.clearAllMocks();
  vi.stubGlobal("XMLHttpRequest", UploadRequest);
  fetcher = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          folders: [{ id: "upload-folder", name: "upload", parent_id: null }],
          jobs: [],
        }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const activity = vi.fn();
  const onUploadComplete = vi.fn();
  const hook = renderHook(() => useUploadFile({ onUploadComplete }), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>
        <UploadActivity.Provider value={activity}>
          {children}
        </UploadActivity.Provider>
      </QueryClientProvider>
    ),
  });
  return { ...hook, invalidate, activity, onUploadComplete };
}

it("registers the original in the upload folder and returns the immutable image URL after success", async () => {
  const { result, invalidate, activity, onUploadComplete } = setup();
  const file = new File(["png"], "picture.png", { type: "image/png" });
  let done: ReturnType<typeof result.current.uploadFile>;
  act(() => {
    done = result.current.uploadFile(file);
  });
  await waitFor(() => expect(requests).toHaveLength(1));
  const xhr = requests[0];
  expect(xhr.url).toBe("/api/upload");
  expect(xhr.body?.get("folder_id")).toBe("upload-folder");
  expect(xhr.body?.get("file")).toBe(file);
  expect(result.current.uploadedFile).toBeUndefined();
  expect(result.current.isUploading).toBe(true);
  act(() =>
    xhr.upload.onprogress({ lengthComputable: true, loaded: 3, total: 4 }),
  );
  expect(result.current.progress).toBe(75);
  await act(async () => {
    xhr.respond(202, {
      state: "success",
      image: { reference, media_type: "image/png" },
    });
    await done;
  });
  expect(result.current.uploadedFile?.url).toBe(reference);
  expect(result.current.isUploading).toBe(false);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["library"] });
  expect(onUploadComplete).toHaveBeenCalledOnce();
  expect(activity.mock.calls).toEqual([[1], [-1]]);
});

it("retains an error rather than returning a temporary URL, and can retry", async () => {
  const { result, onUploadComplete } = setup();
  const file = new File(["png"], "picture.png", { type: "image/png" });
  let done: ReturnType<typeof result.current.uploadFile>;
  act(() => {
    done = result.current.uploadFile(file);
  });
  await waitFor(() => expect(requests).toHaveLength(1));
  await act(async () => {
    requests[0].respond(507, { detail: "空き容量が不足しています。" });
    await done;
  });
  expect(result.current.uploadedFile).toBeUndefined();
  expect(result.current.isUploading).toBe(false);
  expect(onUploadComplete).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith("空き容量が不足しています。");
  act(() => {
    done = result.current.uploadFile(file);
  });
  await waitFor(() => expect(requests).toHaveLength(2));
  await act(async () => {
    requests[1].respond(202, { state: "success", image: { reference } });
    await done;
  });
  expect(result.current.uploadedFile?.url).toBe(reference);
  expect(onUploadComplete).toHaveBeenCalledOnce();
});

it("rejects an upload response without a persisted Wiki image reference", async () => {
  const { result } = setup();
  let done: ReturnType<typeof result.current.uploadFile>;
  act(() => {
    done = result.current.uploadFile(
      new File(["png"], "picture.png", { type: "image/png" }),
    );
  });
  await waitFor(() => expect(requests).toHaveLength(1));
  await act(async () => {
    requests[0].respond(202, {
      state: "queued",
      image: { reference: "blob:temporary" },
    });
    await done;
  });
  expect(result.current.uploadedFile).toBeUndefined();
  expect(toast.error).toHaveBeenCalled();
});

it("keeps non-image attachments on the existing Wiki media endpoint", async () => {
  const { result, invalidate } = setup();
  let done: ReturnType<typeof result.current.uploadFile>;
  act(() => {
    done = result.current.uploadFile(
      new File(["audio"], "voice.mp3", { type: "audio/mpeg" }),
    );
  });
  expect(requests[0].url).toBe("/api/wiki/media");
  expect(requests[0].body?.get("folder_id")).toBeNull();
  await act(async () => {
    requests[0].respond(201, { url: "/api/wiki/media/voice.mp3" });
    await done;
  });
  expect(result.current.uploadedFile?.url).toBe("/api/wiki/media/voice.mp3");
  expect(fetcher).not.toHaveBeenCalled();
  expect(invalidate).not.toHaveBeenCalled();
});
