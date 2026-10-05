import { useMemo, useState } from "react";

interface TreeArticle {
  id: string;
  title: string;
  namespace: string;
  path: string;
}
interface Folder {
  key: string;
  name: string;
  folders: Folder[];
  articles: TreeArticle[];
  count: number;
}

function buildTree(articles: TreeArticle[]): Folder {
  const root: Folder = { key: "", name: "", folders: [], articles: [], count: 0 };
  const find = (parent: Folder, name: string) => {
    const key = `${parent.key}/${name}`;
    let child = parent.folders.find((f) => f.key === key);
    if (!child) {
      child = { key, name, folders: [], articles: [], count: 0 };
      parent.folders.push(child);
    }
    return child;
  };
  for (const a of articles) {
    const dirs = a.path.split("/").filter(Boolean).slice(0, -1);
    let node = find(root, a.namespace);
    node.count++;
    for (const dir of dirs) {
      node = find(node, dir);
      node.count++;
    }
    node.articles.push(a);
  }
  const sort = (f: Folder) => {
    f.folders.sort((a, b) => a.name.localeCompare(b.name, "ja"));
    f.articles.sort((a, b) => a.path.localeCompare(b.path, "ja"));
    f.folders.forEach(sort);
  };
  sort(root);
  return root;
}

function ancestorKeys(a: TreeArticle): string[] {
  const keys: string[] = [];
  let key = `/${a.namespace}`;
  keys.push(key);
  for (const dir of a.path.split("/").filter(Boolean).slice(0, -1)) {
    key += `/${dir}`;
    keys.push(key);
  }
  return keys;
}

export function WikiTree({
  articles,
  currentId,
  expandAll,
  onSelect,
}: {
  articles: TreeArticle[];
  currentId: string | undefined;
  expandAll: boolean;
  onSelect: (id: string) => void;
}) {
  const root = useMemo(() => buildTree(articles), [articles]);
  // true/false = the user's explicit choice; otherwise the default applies.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const current = articles.find((a) => a.id === currentId);
  const forced = useMemo(
    () => new Set(current ? ancestorKeys(current) : []),
    [current],
  );
  const isOpen = (f: Folder, depth: number) =>
    toggled[f.key] ?? (expandAll || depth === 0 || forced.has(f.key));

  function renderFolder(f: Folder, depth: number) {
    const open = isOpen(f, depth);
    return (
      <li key={f.key}>
        <button
          type="button"
          aria-expanded={open}
          className="wiki-tree-folder"
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={() => setToggled({ ...toggled, [f.key]: !open })}
        >
          <span className="wiki-tree-caret" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className="wiki-tree-name">{f.name}</span>
          <span className="wiki-tree-count" aria-label={`${f.count}件`}>
            {f.count}
          </span>
        </button>
        {open && (
          <ul>
            {f.folders.map((c) => renderFolder(c, depth + 1))}
            {f.articles.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                          aria-current={a.id === currentId ? "page" : undefined}
                  className="wiki-tree-article"
                  style={{ paddingLeft: 8 + (depth + 1) * 14 + 14 }}
                  title={`${a.namespace} / ${a.path}`}
                  onClick={() => onSelect(a.id)}
                >
                  {a.title}
                </button>
              </li>
            ))}
          </ul>
        )}
      </li>
    );
  }

  return (
    <ul className="wiki-tree" aria-label="記事のフォルダー">
      {root.folders.map((f) => renderFolder(f, 0))}
    </ul>
  );
}
