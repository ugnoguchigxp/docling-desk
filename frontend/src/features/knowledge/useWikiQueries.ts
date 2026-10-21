import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../lib/api";
import { isHttpError } from "../../lib/http";
import { watch } from "../../lib/polling";
import { queryKeys } from "../../lib/query-keys";
import type { Article, ArticleSummary } from "./types";

/**
 * The Wiki reads. The query cache is the only copy of the catalog and of the
 * open article; nothing else in the feature stores them. Reads are paused
 * while the Wiki screen is hidden.
 */
export function useWikiCatalog(hidden: boolean) {
  return useQuery({
    queryKey: queryKeys.wikiCatalog,
    enabled: !hidden,
    queryFn: ({ signal }) =>
      request<{ articles: ArticleSummary[]; namespaces: string[] }>(
        "/api/wiki/catalog",
        { signal },
      ),
    refetchInterval: (q) => watch(q.state),
  });
}

/** The article is gone (deleted or replaced): reading it again cannot help. */
export const isArticleGone = (error: unknown) =>
  isHttpError(error, 404) || isHttpError(error, 410);

/** Look for changes made elsewhere, but not for an article that is gone. */
export const articleInterval = (state: { error?: unknown }) =>
  isArticleGone(state.error) ? false : watch(state);

export function useWikiArticle(source: string | null, hidden: boolean) {
  return useQuery({
    queryKey: queryKeys.wikiArticle(source),
    enabled: !hidden && !!source,
    queryFn: ({ signal }) =>
      request<Article>(
        `/api/wiki/sources/${encodeURIComponent(source || "")}`,
        { signal },
      ),
    refetchInterval: (q) => articleInterval(q.state),
  });
}

/**
 * What each change to the Wiki makes stale. This is the one place that decides
 * it: an import or a save changes titles and bodies (catalog and articles), a
 * delete drops an entry from the catalog and forgets the deleted article.
 */
export function useWikiInvalidation() {
  const cache = useQueryClient();
  const catalog = () =>
    cache.invalidateQueries({ queryKey: queryKeys.wikiCatalog });
  const articles = () =>
    cache.invalidateQueries({ queryKey: queryKeys.wikiArticles });
  return {
    async afterImport() {
      await catalog();
      await articles();
    },
    async afterDelete(source: string) {
      const key = queryKeys.wikiArticle(source);
      await cache.cancelQueries({ queryKey: key });
      cache.removeQueries({ queryKey: key });
      await catalog();
    },
    afterSave() {
      void catalog();
      void articles();
    },
  };
}
