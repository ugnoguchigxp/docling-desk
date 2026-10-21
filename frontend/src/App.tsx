import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "./app/AppShell";
import {
  activeReturn,
  returnLabel,
  searchToReopen,
  type ReturnPoint,
} from "./app/return-point";
import { Button, StatusMessage } from "./components/ui";
import { library } from "./lib/api";
import { isDocument, originalName } from "./lib/formats";
import { libraryInterval } from "./lib/polling";
import { queryKeys } from "./lib/query-keys";
import { targetRoute, useRoute, type SourceTarget } from "./lib/route";
import type { Language, View } from "./lib/types";
import { Library } from "./features/Library";
import { folderPath } from "./features/library/folders";
import { useUpload } from "./features/Upload";
import { Viewer } from "./features/Viewer";
import { ImageDetails } from "./features/ImageDetails";
import { KnowledgeSearch, Wiki } from "./features/Knowledge";

/**
 * Screens, and whether they stay mounted when they are not shown (R01):
 * - Library and Wiki stay mounted and are hidden, so their filters, selection,
 *   scroll position, drafts and open article survive a trip to a document or to
 *   the other mode. Each shows the shared navigation only while it is shown.
 * - A document viewer, an image's details and the "not found" notice are
 *   destroyed on leaving (keyed by job), so a document is never mounted twice.
 * - The search dialog stays mounted; closed, it keeps what was typed.
 */
export function App() {
  const { route, navigate } = useRoute(),
    query = useQuery({
      queryKey: queryKeys.library,
      queryFn: ({ signal }) => library(signal),
      refetchInterval: (q) => libraryInterval(q.state, route.mode),
    }),
    [message, setMessage] = useState(""),
    [followed, setFollowed] = useState<ReturnPoint | null>(null),
    languages = useRef(new Map<string, Language>()),
    openedFrom = useRef<HTMLElement | null>(null),
    leaving = useRef(false);
  const data = query.data || { folders: [], jobs: [] },
    job =
      route.mode === "library"
        ? data.jobs.find((j) => j.id === route.job)
        : undefined;
  const { refetch } = query,
    inLibrary = useRef(route.mode === "library");
  useEffect(() => {
    // The list is not polled while another mode is shown.
    if (route.mode === "library" && !inLibrary.current)
      void refetch({ cancelRefetch: false });
    inLibrary.current = route.mode === "library";
  }, [route.mode, refetch]);
  const destination = route.job
    ? (job?.folder_id ?? route.folder)
    : route.folder;
  const upload = useUpload(
    destination,
    folderPath(data.folders, destination),
    setMessage,
    route.mode === "library",
  );
  const onView = useCallback(
    (view: View) => {
      navigate({ ...route, view }, false);
    },
    [navigate, route],
  );
  useEffect(() => {
    if (route.mode === "library" && !route.job)
      document.title = "Docling Desk · 資料一覧";
  }, [route.job, route.mode]);
  useEffect(() => {
    if (
      query.data &&
      route.folder &&
      !query.data.folders.some((f) => f.id === route.folder)
    ) {
      navigate({ ...route, folder: null }, false);
      setMessage("フォルダーが見つからないため、資料一覧へ戻りました。");
    }
  }, [query.data, route, navigate]);
  useEffect(() => {
    leaving.current = false;
  }, [route]);

  /** Follow a reference (search hit, citation, link) to its source. */
  function follow(target: SourceTarget, origin: ReturnPoint["origin"]) {
    setFollowed({ origin, from: route, target });
    setMessage("");
    navigate(targetRoute(route, target));
  }
  const returning = activeReturn(followed, route),
    reopenable = searchToReopen(followed, route);
  const inViewer = route.mode === "library" && !!route.job;
  /** Back to where the reference was followed from, once. */
  function goBackToOrigin(point: ReturnPoint) {
    setFollowed(null);
    setMessage("");
    navigate(point.from);
  }
  /** Back from a document: to the reference's origin, else to the list. */
  function back(openSearch: (resume?: boolean) => void) {
    if (!route.job || leaving.current) return;
    leaving.current = true;
    if (returning) {
      goBackToOrigin(returning);
      if (returning.origin === "search") openSearch(true);
      return;
    }
    navigate({ ...route, job: null, view: "preview", unit: null });
    setMessage("");
    requestAnimationFrame(() => {
      if (openedFrom.current?.isConnected) openedFrom.current.focus();
      else document.getElementById("fileSearch")?.focus();
    });
  }
  const libraryShown = route.mode === "library" && (!route.job || !!job?.image),
    unitNotice =
      job &&
      !job.image &&
      route.unit &&
      job.pages > 0 &&
      route.unit > job.pages &&
      !isDocument(originalName(job))
        ? `指定された位置（${route.unit}）は資料の範囲外です。最後の位置を表示しています。`
        : "";
  return (
    <AppShell
      route={route}
      navigate={navigate}
      resume={
        returning && !inViewer
          ? {
              label: returnLabel(returning),
              onResume: () => goBackToOrigin(returning),
              reopenSearch: returning.origin === "search",
            }
          : reopenable
            ? {
                label: "検索結果を開く",
                onResume: () => setFollowed(null),
                reopenSearch: true,
              }
            : null
      }
      overlays={upload.ui}
      renderSearch={(search) => (
        <KnowledgeSearch
          initialAction={search.initialAction}
          resume={search.resume}
          open={search.open}
          onClose={search.onClose}
          route={route}
          onReference={(target) => follow(target, "search")}
        />
      )}
    >
      {({ navigation, openSearch }) => (
        <>
          <Library
            data={data}
            folder={route.folder}
            hidden={!libraryShown}
            scope={route.libraryScope}
            layout={route.libraryView}
            onScope={(scope) =>
              navigate({
                ...route,
                libraryScope: scope,
                libraryView: undefined,
                folder: null,
              })
            }
            onLayout={(layout) =>
              navigate({ ...route, libraryView: layout }, false)
            }
            navigation={libraryShown ? navigation : null}
            message={query.error?.message || message}
            setMessage={setMessage}
            onFolder={(folder) =>
              navigate({ ...route, folder, libraryView: undefined })
            }
            onJob={(job) => {
              openedFrom.current =
                document.activeElement instanceof HTMLElement
                  ? document.activeElement
                  : null;
              setMessage("");
              setFollowed(null);
              navigate({ ...route, job: job.id, view: "preview", unit: null });
              requestAnimationFrame(() =>
                document.getElementById("backToFiles")?.focus(),
              );
            }}
            onUpload={upload.open}
          />
          <Wiki
            route={route}
            navigate={navigate}
            navigation={route.mode === "wiki" ? navigation : null}
            hidden={route.mode !== "wiki"}
            onReference={(target) => follow(target, "wiki")}
          />
          {job?.image && (
            <ImageDetails
              key={job.id}
              job={job}
              onClose={() => back(openSearch)}
            />
          )}
          {job && !job.image && (
            <Viewer
              key={job.id}
              job={job}
              view={route.view}
              onView={onView}
              onBack={() => back(openSearch)}
              backLabel={returning ? returnLabel(returning) : undefined}
              message={query.error?.message || message || unitNotice}
              initialLanguage={languages.current.get(job.id) || "original"}
              onLanguage={(l) => languages.current.set(job.id, l)}
              navigation={navigation}
              initialUnit={route.unit}
            />
          )}{" "}
          {route.mode === "library" && route.job && !job && (
            <section id="detail">
              {navigation}
              <Button id="backToFiles" onClick={() => back(openSearch)}>
                {returning ? returnLabel(returning) : "資料一覧へ戻る"}
              </Button>
              <StatusMessage
                kind={
                  query.isPending ? "loading" : query.error ? "error" : "stale"
                }
                detail={
                  query.isPending || query.error
                    ? undefined
                    : "削除されたか、閲覧できる範囲が変わった可能性があります。別の資料に置き換えて表示することはしません。"
                }
              >
                {query.isPending
                  ? "読み込み中…"
                  : query.error?.message ||
                    "資料が見つかりません。資料一覧へ戻って選び直してください。"}
              </StatusMessage>
            </section>
          )}
        </>
      )}
    </AppShell>
  );
}
