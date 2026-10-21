import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorText, library, request } from "../../lib/api";
import { queryKeys } from "../../lib/query-keys";
import type { Job } from "../../lib/types";
import { diagramFilename, emptyScene } from "./diagram";
import type { OperationLock } from "./useOperationLock";

/**
 * Creating an empty Excalidraw diagram in a folder. The dialog's name input is
 * not held here. One attempt = one id and key, so a retry of the same name and
 * folder cannot create a second diagram.
 */
export function useDiagramCreation({
  folder,
  lock,
  setMessage,
  onJob,
}: {
  folder: string | null;
  lock: OperationLock;
  setMessage: (text: string) => void;
  onJob: (job: Job) => void;
}) {
  const cache = useQueryClient(),
    [target, setTarget] = useState<{ parent: string | null } | null>(null),
    attempt = useRef<{
      id: string;
      key: string;
      name: string;
      parent: string | null;
    } | null>(null);
  async function submit(input: string, onError: (text: string) => void) {
    if (lock.isBusy() || !target) return;
    const filename = diagramFilename(input);
    if (
      attempt.current?.name !== filename ||
      attempt.current.parent !== target.parent
    )
      attempt.current = {
        id: crypto.randomUUID().replaceAll("-", ""),
        key: crypto.randomUUID(),
        name: filename,
        parent: target.parent,
      };
    const { id, key } = attempt.current;
    if (!lock.acquire()) return;
    try {
      await request(`/api/diagrams/${id}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "If-None-Match": "*",
          "Idempotency-Key": key,
        },
        body: JSON.stringify({
          name: filename,
          folder_id: target.parent,
          scene: emptyScene(),
        }),
      });
      attempt.current = null;
      setTarget(null);
      setMessage("図表を作成しました。");
      const fresh = await cache
        .fetchQuery({
          queryKey: queryKeys.library,
          queryFn: ({ signal }) => library(signal),
          staleTime: 0,
        })
        .catch(() => null);
      const job = fresh?.jobs.find((j) => j.id === id);
      if (job) onJob(job);
      else setMessage("図表を作成しました。一覧から開いてください。");
    } catch (e) {
      onError(errorText(e));
    } finally {
      lock.release();
    }
  }
  return {
    target,
    start() {
      if (lock.isBusy()) return;
      setTarget({ parent: folder });
    },
    cancel: () => setTarget(null),
    submit,
  };
}
