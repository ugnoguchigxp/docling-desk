import { Button } from "../components/ui";
import { Icon } from "../components/Icons";
import type { Route } from "../lib/route";
import "./app-navigation.css";

const shortcutLabel = /Mac|iPhone|iPad/.test(
  globalThis.navigator?.platform ?? "",
)
  ? "⌘K"
  : "Ctrl K";

/**
 * The app-level mode switch and the search / RAG entries (R01).
 *
 * Navigation only: it takes the current mode and reports what the person
 * picked. It reads no API and knows nothing of Library, Wiki or search state,
 * so the shared app frame can render it in any normal screen's header.
 * The embed entry never renders it.
 */
export function AppNavigation({
  mode,
  onMode,
  onSearch,
  onRag,
  resume,
}: {
  mode: Route["mode"];
  onMode: (mode: Route["mode"]) => void;
  onSearch: () => void;
  onRag?: () => void;
  /** Back to where a reference was followed from; absent when there is none. */
  resume?: { label: string; onResume: () => void } | null;
}) {
  return (
    <>
      <nav className="mode-menu" data-mode={mode} aria-label="表示モード">
        <Button
          className="mode-menu-option"
          aria-current={mode === "library" ? "page" : undefined}
          onClick={() => onMode("library")}
        >
          資料一覧
        </Button>
        <Button
          className="mode-menu-option"
          aria-current={mode === "wiki" ? "page" : undefined}
          onClick={() => onMode("wiki")}
        >
          Wiki
        </Button>
      </nav>
      {resume && (
        <Button className="global-resume" onClick={resume.onResume}>
          {resume.label}
        </Button>
      )}
      <button
        type="button"
        className="global-search"
        title="本文を検索"
        onClick={onSearch}
      >
        <Icon name="search" />
        <span>本文を検索</span>
        <kbd aria-hidden="true">{shortcutLabel}</kbd>
      </button>
      {onRag && (
        <Button className="global-rag" onClick={onRag}>
          RAG
        </Button>
      )}
    </>
  );
}
