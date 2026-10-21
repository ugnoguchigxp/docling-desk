import { useEffect, useRef, useState, type ReactNode } from "react";
import { IconDefinitions } from "../components/Icons";
import type { Route } from "../lib/route";
import { AppNavigation } from "./AppNavigation";

export type SearchAction = "search" | "rag";

/** What the shell hands the dialog it does not know: the search dialog. */
export interface SearchState {
  open: boolean;
  initialAction: SearchAction;
  /** Open with the last results instead of an empty search (R05 return). */
  resume: boolean;
  onClose: () => void;
}

/** A return control shown in the shared navigation (R05). */
export interface Resume {
  label: string;
  onResume: () => void;
  /** Also reopen the search dialog with its last results. */
  reopenSearch?: boolean;
}

/**
 * The frame of every normal screen (R01): the icon sprite, `<main>`, the one
 * navigation (mode switch, search, RAG) each screen shows in its own header,
 * the Ctrl/Cmd+K shortcut, and the search dialog.
 *
 * It owns only navigation state: which mode was last used where (so switching
 * back returns to the same article or document), and whether search is open.
 * Screen bodies come in through `children` and are mounted once by the caller;
 * the shell never renders a body twice. The search dialog is injected so the
 * frame imports neither Wiki nor search code. The embed entry (`src/embed/`)
 * and the standalone editor do not use this component.
 */
export function AppShell({
  route,
  navigate,
  renderSearch,
  overlays,
  resume,
  children,
}: {
  route: Route;
  navigate: (route: Route) => void;
  renderSearch: (search: SearchState) => ReactNode;
  /** Dialogs of the screens (upload), rendered after `<main>`. */
  overlays?: ReactNode;
  resume?: Resume | null;
  /** `navigation` is one element; hand it only to the screen that is shown. */
  children: (frame: {
    navigation: ReactNode;
    /** Open search; `resume` keeps the last results (R05 return). */
    openSearch: (resume?: boolean) => void;
  }) => ReactNode;
}) {
  const [search, setSearch] = useState({
      open: false,
      action: "search" as SearchAction,
      resume: false,
    }),
    locations = useRef<Partial<Record<Route["mode"], Route>>>({});
  useEffect(() => {
    locations.current[route.mode] = route;
  }, [route]);
  const open = (action: SearchAction, resume = false) =>
    setSearch({ open: true, action, resume });
  useEffect(() => {
    // One document-level listener. A key another handler already used (an
    // editor's own Ctrl+K) or one that is only being held down does nothing.
    const onKey = (e: KeyboardEvent) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key.toLowerCase() === "k" &&
        !e.defaultPrevented &&
        !e.repeat
      ) {
        e.preventDefault();
        setSearch({ open: true, action: "search", resume: false });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const navigation = (
    <AppNavigation
      mode={route.mode}
      resume={
        resume && {
          label: resume.label,
          onResume: () => {
            resume.onResume();
            if (resume.reopenSearch) open("search", true);
          },
        }
      }
      onSearch={() => open("search")}
      onRag={() => open("rag")}
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
  return (
    <>
      <IconDefinitions />
      <main>
        {children({
          navigation,
          openSearch: (resume = false) => open("search", resume),
        })}
      </main>
      {overlays}
      {renderSearch({
        open: search.open,
        initialAction: search.action,
        resume: search.resume,
        onClose: () => setSearch((s) => ({ ...s, open: false, resume: false })),
      })}
    </>
  );
}
