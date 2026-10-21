import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, post, request } from "../../lib/api";
import { useAsyncScope } from "../../lib/async-scope";
import {
  WATCH_MS,
  pollInterval,
  running,
  searchInterval,
} from "../../lib/polling";
import { queryKeys } from "../../lib/query-keys";
import type { Route } from "../../lib/route";
import type { IndexStatus, Task } from "./types";

export type Operation = "search" | "rag";
export type Scope = "all" | "current" | "folder";

/** What "current" and "folder" mean on this route, and the scope that is usable. */
export function resolveScope(route: Route, scope: string) {
  const currentSource =
    route.mode === "wiki"
      ? route.source
      : route.job
        ? `doc-${route.job}`
        : null;
  const hasFolder = route.mode === "library" && !!route.folder;
  const effective =
    (scope === "current" && !currentSource) ||
    (scope === "folder" && !hasFolder)
      ? "all"
      : scope;
  return { currentSource, effective };
}

export interface SearchInput {
  query: string;
  mode: string;
  kind: string;
  scope: string;
}

/**
 * The single owner of one search / RAG dialog's server work: the index status,
 * the running retrieval, and what happens to it when the person cancels,
 * leaves, or the sources change.
 *
 * - The retrieval itself lives in the query cache under its id. Only the id is
 *   kept here, so the cache is the one copy of the task: the start response
 *   and a cancel response are written into it, polling replaces it.
 * - Every action starts an attempt in one scope. Moving to another document,
 *   closing, or starting another action expires the earlier attempt, so a late
 *   reply is never shown. Answers and rankings are made by the server; this
 *   only starts, polls and cancels them.
 */
export function useKnowledgeRetrieval({
  open,
  route,
  initialAction,
  resume = false,
}: {
  open: boolean;
  route: Route;
  initialAction: Operation;
  /**
   * Open on the last retrieval instead of an empty search (coming back from a
   * source, R05). The retrieval is still the one in the query cache; only its
   * id is kept, so a task that finished, went stale or was cancelled while the
   * person was away shows its current server state.
   */
  resume?: boolean;
}) {
  const cache = useQueryClient();
  const [retrievalId, setRetrievalId] = useState<string | null>(null),
    [operation, setOperation] = useState<Operation>(initialAction),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const action = useAsyncScope(
    `${open}:${route.mode}:${route.job}:${route.source}:${route.folder}`,
  );
  const inFlight = useRef(false);
  useEffect(() => {
    inFlight.current = false;
    setBusy(false);
    setError("");
    // A plain open starts empty. Closing keeps the id, so resuming can show it.
    if (open && !resume) {
      setRetrievalId(null);
      setOperation(initialAction);
    }
  }, [
    open,
    resume,
    route.mode,
    route.job,
    route.source,
    route.folder,
    initialAction,
  ]);

  const status = useQuery({
    queryKey: queryKeys.knowledgeStatus,
    enabled: open,
    queryFn: ({ signal }) =>
      request<IndexStatus>("/api/knowledge/status", { signal }),
    refetchInterval: (q) =>
      pollInterval(q.state, {
        busy: 2000,
        isBusy: (data) => data.jobs.some((job) => running(job.state)),
        idle: WATCH_MS,
      }),
  });
  const task = useQuery({
    queryKey: queryKeys.knowledgeSearch(retrievalId ?? undefined),
    enabled: open && !!retrievalId,
    // The start response is written into the cache; ask again at once anyway.
    staleTime: 0,
    queryFn: async ({ signal }) => {
      const value = await request<Task>(
        `/api/knowledge/retrievals/${retrievalId}`,
        { signal },
      );
      // The cache entry is named by the id; another task never replaces it.
      if (value.id !== retrievalId)
        throw new Error("検索結果の取得先が一致しません。");
      return value;
    },
    refetchInterval: (q) => searchInterval(q.state),
  });
  const current = retrievalId ? (task.data ?? null) : null;
  const waiting = !!current && running(current.state);
  const indexJob = status.data?.jobs.find((j) => running(j.state));

  async function search(input: SearchInput, next: Operation = "search") {
    if (!input.query.trim() || inFlight.current || waiting) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setOperation(next);
    setRetrievalId(null);
    const isCurrent = action.start();
    const { currentSource, effective } = resolveScope(route, input.scope);
    try {
      const value = await post<Task>(
        next === "rag" ? "/api/knowledge/answers" : "/api/knowledge/search",
        {
          query: input.query,
          mode: input.mode,
          kind: input.kind,
          source_id: effective === "current" ? currentSource : null,
          folder_id: effective === "folder" ? route.folder : null,
          client_request_id: crypto.randomUUID(),
        },
      );
      if (isCurrent()) {
        cache.setQueryData(queryKeys.knowledgeSearch(value.id), value);
        setRetrievalId(value.id);
      }
    } catch (e) {
      if (isCurrent()) setError(errorText(e));
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }
  async function index() {
    if (inFlight.current) return;
    inFlight.current = true;
    const isCurrent = action.start();
    setBusy(true);
    setError("");
    try {
      await post("/api/knowledge/index-jobs", {});
      await status.refetch();
    } catch (e) {
      if (isCurrent()) setError(errorText(e));
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }
  /** Stop a running retrieval or the index job; the server's answer replaces it. */
  async function cancel(id: string, kind: "retrieval" | "index") {
    if (inFlight.current) return;
    inFlight.current = true;
    const isCurrent = action.start();
    setBusy(true);
    try {
      const value = await post<Task>(`/api/knowledge/tasks/${id}/cancel`, {});
      if (kind === "retrieval") {
        await cache.cancelQueries({ queryKey: queryKeys.knowledgeSearch(id) });
        cache.setQueryData(queryKeys.knowledgeSearch(id), value);
        if (isCurrent()) setRetrievalId(value.id);
      } else await status.refetch();
    } catch (e) {
      if (isCurrent()) setError(errorText(e));
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }

  return {
    status,
    taskError: task.error,
    current,
    waiting,
    indexJob,
    operation,
    busy,
    error,
    search,
    index,
    cancel,
    /** Expire whatever is still on its way (the dialog is being closed). */
    stop: action.invalidate,
  };
}
