import { useEffect, useRef, useState } from "react";
import { errorText, request } from "../../lib/api";
import { useAsyncScope } from "../../lib/async-scope";
import { isWikiArticle } from "../../lib/formats";
import type { Route } from "../../lib/route";
import type { WikiDraft } from "../wiki-draft";
import type { ArticleSummary } from "./types";
import { useWikiInvalidation } from "./useWikiQueries";

/**
 * The Wiki's changing requests (import, delete, open for editing) and their
 * one busy / message state. A request that finishes after the person moved to
 * another article or hid the screen reports nothing; the lists learn of the
 * change through `useWikiInvalidation`, never by this hook editing them.
 * The callbacks run only while the request is still current.
 */
export function useWikiActions({
  hidden,
  route,
  navigate,
}: {
  hidden: boolean;
  route: Route;
  navigate: (route: Route) => void;
}) {
  const invalidate = useWikiInvalidation();
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const action = useAsyncScope(`${hidden}:${route.source}`);
  const actionLock = useRef(false);
  // What an editor save that finishes later has to know about the screen now.
  const present = useRef({ hidden, route });
  present.current = { hidden, route };
  // A request already sent keeps running when the screen changes: the lock is
  // released only when it settles (see `exclusive`), so the same change is not
  // sent twice. Its result is ignored once it is no longer current.
  useEffect(() => {
    setMessage("");
  }, [hidden, route.source]);

  /** Run one exclusive request; its callbacks only fire while it is current. */
  async function exclusive(
    run: (isCurrent: ReturnType<typeof action.start>) => Promise<void>,
    options: { clearMessage?: boolean } = {},
  ) {
    if (actionLock.current) return;
    actionLock.current = true;
    const isCurrent = action.start();
    setBusy(true);
    if (options.clearMessage) setMessage("");
    try {
      await run(isCurrent);
    } catch (error) {
      if (isCurrent()) setMessage(errorText(error));
    } finally {
      actionLock.current = false;
      setBusy(false);
    }
  }

  async function importFiles(
    files: File[],
    namespace: string,
    onImported: () => void,
  ) {
    if (actionLock.current || !files.some((f) => isWikiArticle(f.name))) return;
    await exclusive(
      async (isCurrent) => {
        const data = new FormData();
        data.set("namespace", namespace);
        for (const file of files) {
          if (["wiki-manifest.json", "pages.jsonl"].includes(file.name))
            data.append("manifest", file);
          else
            data.append(
              "files",
              file,
              file.webkitRelativePath
                ? file.webkitRelativePath.split("/").slice(1).join("/")
                : file.name,
            );
        }
        const result = await request<{ articles: ArticleSummary[] }>(
          "/api/wiki/import",
          { method: "POST", body: data },
        );
        await invalidate.afterImport();
        if (!isCurrent()) return;
        onImported();
        setMessage(`${result.articles.length}件の記事を取り込みました。`);
        if (result.articles[0])
          navigate({
            ...route,
            mode: "wiki",
            source: result.articles[0].id,
            section: null,
          });
      },
      { clearMessage: true },
    );
  }

  async function deleteArticle(target: string | null, onDeleted: () => void) {
    if (!target) return;
    await exclusive(async (isCurrent) => {
      await request(`/api/wiki/sources/${encodeURIComponent(target)}`, {
        method: "DELETE",
      });
      await invalidate.afterDelete(target);
      if (!isCurrent()) return;
      onDeleted();
      navigate({ ...route, source: null, section: null });
      setMessage("記事を削除しました。");
    });
  }

  async function loadDraft(onLoaded: (draft: WikiDraft) => void) {
    if (!route.source) return;
    const source = route.source;
    await exclusive(
      async (isCurrent) => {
        const draft = await request<WikiDraft>(
          `/api/wiki/sources/${encodeURIComponent(source)}/edit`,
          { signal: isCurrent.signal },
        );
        if (isCurrent()) onLoaded(draft);
      },
      { clearMessage: true },
    );
  }

  /** The editor stored an article: the lists learn of it, a moved-on screen is left alone. */
  function saved(article: { id: string }) {
    invalidate.afterSave();
    if (present.current.hidden) return;
    setMessage("Wikiに保存しました。");
    navigate({
      ...present.current.route,
      mode: "wiki",
      source: article.id,
      section: null,
    });
  }

  return {
    busy,
    message,
    setMessage,
    importFiles,
    deleteArticle,
    loadDraft,
    saved,
  };
}
