import type { RefObject } from "react";
import { Button, SelectField, StatusMessage } from "../../components/ui";
import { withBase } from "../../lib/base";
import { linkTarget, type Route, type SourceTarget } from "../../lib/route";
import { WikiPlate } from "../../editor/WikiPlate";
import type { Article, ArticleSummary } from "./types";

/**
 * One open article: its bar (language, related document, save / edit /
 * delete), the outline and the body. Display only: the article and the
 * catalog come from the queries, and every action is reported upward.
 */
export function WikiArticleView({
  article,
  siblings,
  route,
  source,
  navigate,
  onReference,
  busy,
  bodyRef,
  onEdit,
  onDelete,
  onNotice,
}: {
  article: Article;
  /** Catalog entries, to offer the other languages of this article. */
  siblings: ArticleSummary[];
  route: Route;
  /** The open article id (`route.source`, known to be set). */
  source: string;
  navigate: (route: Route) => void;
  /** Open another source (a related document, a link in the body). */
  onReference: (target: SourceTarget) => void;
  busy: boolean;
  bodyRef: RefObject<HTMLDivElement | null>;
  onEdit: () => void;
  onDelete: () => void;
  onNotice: (message: string) => void;
}) {
  return (
    <>
      <div className="wiki-article-bar">
        <span>
          {article.namespace} / {article.path}
        </span>
        {article.translation_group && (
          <SelectField
            aria-label="記事の言語"
            value={source}
            onChange={(e) =>
              navigate({
                ...route,
                source: e.target.value,
                section: null,
              })
            }
          >
            {siblings
              .filter(
                (a) =>
                  a.namespace === article.namespace &&
                  a.translation_group === article.translation_group,
              )
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.language || a.path}
                  {a.translation_status === "untranslated"
                    ? "（未翻訳）"
                    : a.translation_status === "needs_review"
                      ? "（要確認）"
                      : ""}
                </option>
              ))}
          </SelectField>
        )}
        {article.related_document && (
          <Button
            onClick={() =>
              onReference({
                kind: "document",
                job: article.related_document?.job_id ?? "",
                unit: article.source_unit || 1,
              })
            }
          >
            関連資料：{article.related_document.title}
          </Button>
        )}
        <a
          href={withBase(
            `/api/wiki/sources/${encodeURIComponent(source)}/markdown`,
          )}
        >
          {article.entry_kind === "csv"
            ? "原本を保存（CSV）"
            : "原本を保存（Markdown）"}
        </a>
        {article.entry_kind !== "csv" && (
          <Button disabled={busy} onClick={onEdit}>
            編集
          </Button>
        )}
        <Button className="danger" onClick={onDelete}>
          記事を削除
        </Button>
      </div>
      {article.fallback_original && (
        <StatusMessage kind="partial">
          日本語訳は未公開です。原文を表示しています。
        </StatusMessage>
      )}
      <div className="wiki-reading-layout">
        <nav className="wiki-outline" aria-label="記事の目次">
          <strong>目次</strong>
          <ul>
            {article.outline.map((h) => (
              <li key={h.anchor} data-level={h.level}>
                <a
                  href={`/?mode=wiki&source=${encodeURIComponent(source)}&section=${encodeURIComponent(h.anchor)}`}
                  onClick={(e) => {
                    e.preventDefault();
                    navigate({ ...route, section: h.anchor });
                  }}
                >
                  {h.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div
          ref={bodyRef}
          className="wiki-body"
          onClick={(e) => {
            const anchor = (e.target as HTMLElement).closest<HTMLAnchorElement>(
              "a",
            );
            if (
              !anchor ||
              e.metaKey ||
              e.ctrlKey ||
              e.shiftKey ||
              e.altKey ||
              anchor.target === "_blank"
            )
              return;
            if (anchor.getAttribute("aria-disabled") === "true") {
              e.preventDefault();
              onNotice(anchor.title || "リンク先を参照できません。");
              return;
            }
            const href = anchor.getAttribute("href");
            if (!href) return;
            if (href.startsWith("#")) {
              e.preventDefault();
              navigate({ ...route, section: href.slice(1) || null });
              return;
            }
            const url = new URL(href, location.href);
            if (
              url.origin !== location.origin ||
              !["/", withBase("/")].includes(url.pathname)
            )
              return;
            e.preventDefault();
            const target = linkTarget(href, location);
            if (target) {
              onReference(target);
              return;
            }
            navigate({
              ...route,
              mode:
                url.searchParams.get("mode") === "wiki" ? "wiki" : "library",
              source: url.searchParams.get("source"),
              section: url.searchParams.get("section"),
              job: url.searchParams.get("job"),
              view: "preview",
              unit: Number(url.searchParams.get("unit")) || null,
            });
          }}
        >
          {article.article_info && (
            <>
              <h1 className="wiki-article-title">{article.title}</h1>
              {article.article_info.subtitle && (
                <p className="wiki-article-subtitle">
                  {article.article_info.subtitle}
                </p>
              )}
            </>
          )}
          {article.editor_document ? (
            <WikiPlate
              key={article.id + article.revision + article.updated}
              initial={article.body}
              document={article.editor_document}
              outline={article.outline}
              renderedHtml={article.html}
              readOnly
              preview
            />
          ) : (
            <div dangerouslySetInnerHTML={{ __html: article.html }} />
          )}
        </div>
      </div>
    </>
  );
}
