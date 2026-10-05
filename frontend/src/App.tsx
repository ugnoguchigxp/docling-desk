import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { IconDefinitions } from "./components/Icons";
import { Button, StatusMessage } from "./components/ui";
import { library } from "./lib/api";
import { useRoute, type Route } from "./lib/route";
import type { Language, View } from "./lib/types";
import { folderPath, Library } from "./features/Library";
import { useUpload } from "./features/Upload";
import { Viewer } from "./features/Viewer";
import { KnowledgeSearch, ModeMenu, Wiki } from "./features/Knowledge";
export function App() {
  const { route, navigate } = useRoute(),
    query = useQuery({
      queryKey: ["library"],
      queryFn: ({ signal }) => library(signal),
      refetchInterval: 2000,
    }),
    [message, setMessage] = useState(""),
    [searchOpen, setSearchOpen] = useState(false),
    locations = useRef<Partial<Record<Route["mode"], Route>>>({}),
    languages = useRef(new Map<string, Language>()),
    openedFrom = useRef<HTMLElement | null>(null);
  const data = query.data || { folders: [], jobs: [] },
    job =
      route.mode === "library"
        ? data.jobs.find((j) => j.id === route.job)
        : undefined;
  useEffect(() => {
    locations.current[route.mode] = route;
  }, [route]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const menu = (
    <ModeMenu
      mode={route.mode}
      onSearch={() => setSearchOpen(true)}
      onMode={(mode) => {
        if (mode === route.mode) return;
        navigate(
          locations.current[mode] || {
            ...route,
            mode,
            job: null,
            source: null,
            section: null,
            unit: null,
          },
        );
      }}
    />
  );
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
  function back() {
    navigate({ ...route, job: null, view: "preview", unit: null });
    setMessage("");
    requestAnimationFrame(() => {
      if (openedFrom.current?.isConnected) openedFrom.current.focus();
      else document.getElementById("fileSearch")?.focus();
    });
  }
  return (
    <>
      <IconDefinitions />
      <main>
        <Library
          data={data}
          folder={route.folder}
          hidden={route.mode !== "library" || !!route.job}
          navigation={menu}
          message={query.error?.message || message}
          setMessage={setMessage}
          onFolder={(folder) => navigate({ ...route, folder })}
          onJob={(job) => {
            openedFrom.current =
              document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null;
            setMessage("");
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
          navigation={menu}
          hidden={route.mode !== "wiki"}
        />
        {job && (
          <Viewer
            key={job.id}
            job={job}
            view={route.view}
            onView={onView}
            onBack={back}
            message={query.error?.message || message}
            initialLanguage={languages.current.get(job.id) || "original"}
            onLanguage={(l) => languages.current.set(job.id, l)}
            navigation={menu}
            initialUnit={route.unit}
          />
        )}{" "}
        {route.mode === "library" && route.job && !job && (
          <section id="detail">
            {menu}
            <Button id="backToFiles" onClick={back}>
              資料一覧へ戻る
            </Button>
            <StatusMessage>
              {query.isPending
                ? "読み込み中…"
                : query.error?.message ||
                  "資料が見つかりません。資料一覧へ戻って選び直してください。"}
            </StatusMessage>
          </section>
        )}
      </main>
      {upload.ui}
      <KnowledgeSearch
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        route={route}
        navigate={navigate}
      />
    </>
  );
}
