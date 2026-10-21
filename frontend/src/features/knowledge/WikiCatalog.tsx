import { SelectField, TextField } from "../../components/ui";
import { WikiTree } from "../WikiTree";
import type { ArticleSummary } from "./types";

/** The article list: filters and the tree. Selection is reported, not handled. */
export function WikiCatalog({
  articles,
  namespaces,
  pending,
  filter,
  namespaceFilter,
  currentId,
  onFilter,
  onNamespaceFilter,
  onSelect,
}: {
  articles: ArticleSummary[];
  namespaces: string[];
  /** The catalog has not been read yet. */
  pending: boolean;
  filter: string;
  namespaceFilter: string;
  currentId: string | undefined;
  onFilter: (value: string) => void;
  onNamespaceFilter: (value: string) => void;
  onSelect: (id: string) => void;
}) {
  const shown = articles.filter(
    (a) =>
      (!namespaceFilter || a.namespace === namespaceFilter) &&
      `${a.title} ${a.path}`
        .toLocaleLowerCase()
        .includes(filter.toLocaleLowerCase()),
  );
  return (
    <aside className="wiki-catalog" aria-label="Wikiの記事一覧">
      <TextField
        type="search"
        aria-label="Wikiの記事名を絞り込む"
        placeholder="記事名を絞り込む"
        value={filter}
        onChange={(e) => onFilter(e.target.value)}
      />
      <SelectField
        aria-label="Wikiの分類"
        value={namespaceFilter}
        onChange={(e) => onNamespaceFilter(e.target.value)}
      >
        <option value="">すべての分類</option>
        {namespaces.map((n) => (
          <option key={n}>{n}</option>
        ))}
      </SelectField>
      {pending && <p>記事一覧を読み込み中…</p>}
      {!pending && !shown.length && (
        <p>
          記事がありません。新しく作成するか、Markdownファイルを取り込んでください。
        </p>
      )}
      <WikiTree
        articles={shown}
        currentId={currentId}
        expandAll={!!filter || !!namespaceFilter}
        onSelect={onSelect}
      />
    </aside>
  );
}
