/**
 * Query keys, one place. A key names the server resource (document, version,
 * setting) and nothing about the screen showing it, so every screen that reads
 * the same resource shares one cache entry and one invalidation.
 *
 * Which mutation invalidates what:
 *   library        upload, move, delete, duplicate, diagram create, image
 *                  attribute save (and the poll while jobs run)
 *   wikiCatalog    Wiki save / delete / import   (+ wikiArticle for edits)
 *   translations   starting a translation
 *   explanations   generating or regenerating an explanation
 * `featureDiagnostics` is read when the status dialog opens and when the
 * person asks again after a recovery; it is never polled or invalidated.
 * `knowledgeSearch` is only ever cancelled, never invalidated: a finished
 * search is a stored result, and the server marks it stale itself.
 */
export const queryKeys = {
  library: ["library"] as const,
  wikiCatalog: ["wiki-catalog"] as const,
  /** Prefix of every article: for invalidation after a save or delete. */
  wikiArticles: ["wiki-article"] as const,
  wikiArticle: (source: string | null) => ["wiki-article", source] as const,
  knowledgeStatus: ["knowledge-status"] as const,
  featureDiagnostics: ["feature-diagnostics"] as const,
  knowledgeSearch: (id: string | undefined) =>
    ["knowledge-search", id] as const,
  translations: (job: string) => ["translations", job] as const,
  translationResult: (
    job: string,
    unit: string | undefined,
    language: string,
    revision: string,
  ) => ["translation-result", job, unit, language, revision] as const,
  explanations: (job: string) => ["explanations", job] as const,
  explanationResult: (
    job: string,
    unit: string | undefined,
    version: string | undefined,
  ) => ["explanation-result", job, unit, version] as const,
};
