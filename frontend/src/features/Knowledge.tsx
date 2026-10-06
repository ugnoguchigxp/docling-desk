import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Dialog,
  DialogActions,
  DialogHeading,
  SelectField,
  StatusMessage,
  TextField,
  Toolbar,
} from "../components/ui";
import { Icon } from "../components/Icons";
import { errorText, post, request } from "../lib/api";
import { withBase } from "../lib/base";
import { useAsyncScope } from "../lib/async-scope";
import type { Route } from "../lib/route";
import { WikiTree } from "./WikiTree";
import "./knowledge.css";

interface ArticleSummary {
  id: string;
  title: string;
  namespace: string;
  path: string;
  revision: string;
  language?: string;
  translation_group?: string;
  source_job_id?: string;
  source_unit?: number;
  translation_status?:
    "untranslated" | "translated" | "reviewed" | "needs_review";
  entry_kind?: "csv";
}
interface Article extends ArticleSummary {
  body: string;
  html: string;
  outline: { title: string; anchor: string; level: number }[];
  related_document: { job_id: string; title: string } | null;
  fallback_original?: string | null;
}
interface Hit {
  chunk_id: string;
  source_id: string;
  kind: "wiki" | "document";
  title: string;
  text: string;
  job_id: string | null;
  unit: number;
  partial: boolean;
  locator: { anchor?: string; heading?: string; refs?: string[] };
}
interface Task {
  id: string;
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  error?: string;
  progress: number;
  total: number;
  output: {
    results?: Hit[];
    notice?: string;
    stale?: boolean;
    answer?: string;
    citations?: (Hit & { label: string })[];
    unknowns?: string[];
  };
}
interface IndexStatus {
  azure_configured: boolean;
  configuration_error: string;
  chunks: number;
  embedded: number;
  cooldown_until: number | null;
  jobs: Task[];
}

const shortcutLabel = /Mac|iPhone|iPad/.test(
  globalThis.navigator?.platform ?? "",
)
  ? "⌘K"
  : "Ctrl K";

export function ModeMenu({
  mode,
  onMode,
  onSearch,
  onRag,
}: {
  mode: Route["mode"];
  onMode: (mode: Route["mode"]) => void;
  onSearch: () => void;
  onRag?: () => void;
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

export function Wiki({
  route,
  navigate,
  navigation,
  hidden,
}: {
  route: Route;
  navigate: (route: Route) => void;
  navigation: ReactNode;
  hidden: boolean;
}) {
  const cache = useQueryClient();
  const catalog = useQuery({
    queryKey: ["wiki-catalog"],
    enabled: !hidden,
    queryFn: ({ signal }) =>
      request<{ articles: ArticleSummary[]; namespaces: string[] }>(
        "/api/wiki/catalog",
        { signal },
      ),
    refetchInterval: 5000,
  });
  const article = useQuery({
    queryKey: ["wiki-article", route.source],
    enabled: !hidden && !!route.source,
    queryFn: ({ signal }) =>
      request<Article>(
        `/api/wiki/sources/${encodeURIComponent(route.source || "")}`,
        { signal },
      ),
    refetchInterval: 5000,
  });
  const [filter, setFilter] = useState(""),
    [namespaceFilter, setNamespaceFilter] = useState("");
  const [importOpen, setImportOpen] = useState(false),
    [namespace, setNamespace] = useState("Wiki"),
    [files, setFiles] = useState<File[]>([]),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const action = useAsyncScope(`${hidden}:${route.source}`);
  const actionLock = useRef(false);
  const deleteTarget = useRef<string | null>(null);
  useEffect(() => {
    actionLock.current = false;
    setBusy(false);
    setImportOpen(false);
    setDeleteOpen(false);
    setMessage("");
  }, [hidden, route.source]);
  const articleTitle = article.data?.title,
    articleRevision = article.data?.revision;
  useEffect(() => {
    if (hidden) return;
    document.title = `${articleTitle || "Wiki"} · Docling Desk`;
    if (route.section && articleRevision) {
      const heading = [
        ...(body.current?.querySelectorAll<HTMLElement>("[id]") || []),
      ].find((node) => node.id === route.section);
      heading?.scrollIntoView({ block: "start" });
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    } else body.current?.scrollTo({ top: 0 });
  }, [hidden, articleRevision, articleTitle, route.source, route.section]);
  async function importFiles() {
    if (
      actionLock.current ||
      !files.some((f) => /\.(md|markdown|csv)$/i.test(f.name))
    )
      return;
    actionLock.current = true;
    const isCurrent = action.start();
    setBusy(true);
    setMessage("");
    try {
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
      await cache.invalidateQueries({ queryKey: ["wiki-catalog"] });
      await cache.invalidateQueries({ queryKey: ["wiki-article"] });
      if (!isCurrent()) return;
      setImportOpen(false);
      setFiles([]);
      setMessage(`${result.articles.length}件の記事を取り込みました。`);
      if (result.articles[0])
        navigate({
          ...route,
          mode: "wiki",
          source: result.articles[0].id,
          section: null,
        });
    } catch (error) {
      if (isCurrent()) setMessage(errorText(error));
    } finally {
      if (isCurrent()) {
        actionLock.current = false;
        setBusy(false);
      }
    }
  }
  async function deleteArticle() {
    const target = deleteTarget.current;
    if (!target || actionLock.current) return;
    actionLock.current = true;
    const isCurrent = action.start();
    setBusy(true);
    try {
      await request(`/api/wiki/sources/${encodeURIComponent(target)}`, {
        method: "DELETE",
      });
      await cache.invalidateQueries({ queryKey: ["wiki-catalog"] });
      if (!isCurrent()) return;
      setDeleteOpen(false);
      navigate({ ...route, source: null, section: null });
      setMessage("記事を削除しました。");
    } catch (error) {
      if (isCurrent()) setMessage(errorText(error));
    } finally {
      if (isCurrent()) {
        actionLock.current = false;
        setBusy(false);
      }
    }
  }
  const articles = (catalog.data?.articles || []).filter(
    (a) =>
      (!namespaceFilter || a.namespace === namespaceFilter) &&
      `${a.title} ${a.path}`
        .toLocaleLowerCase()
        .includes(filter.toLocaleLowerCase()),
  );
  return (
    <section
      className="wiki-screen"
      hidden={hidden}
      aria-label="Wiki"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.stopPropagation();
        if (busy) {
          setMessage("取り込み中です。完了後に追加してください。");
          return;
        }
        const dropped = [...e.dataTransfer.files];
        const accepted = dropped.filter(
          (f) =>
            /\.(md|markdown|csv)$/i.test(f.name) ||
            ["wiki-manifest.json", "pages.jsonl"].includes(f.name),
        );
        if (!accepted.some((f) => /\.(md|markdown|csv)$/i.test(f.name))) {
          setMessage(
            "WikiにはMarkdown記事またはCSV目次を取り込んでください。フォルダーは取り込み画面から選べます。",
          );
          return;
        }
        setFiles(accepted);
        setImportOpen(true);
        setMessage(
          dropped.length !== accepted.length
            ? "Markdown記事・CSV目次・manifest以外の資料は取り込み対象から除外しました。"
            : "",
        );
      }}
    >
      <Toolbar as="header" className="library-bar wiki-bar">
        <h1>
          <Icon name="file" />
          Wiki
        </h1>
        {navigation}
        <Button
          className="primary wiki-import"
          onClick={() => {
            setMessage("");
            setImportOpen(true);
          }}
        >
          Markdownを取り込む
        </Button>
      </Toolbar>
      <StatusMessage className="wiki-message">
        {message || catalog.error?.message || ""}
      </StatusMessage>
      <div className="wiki-layout">
        <aside className="wiki-catalog" aria-label="Wikiの記事一覧">
          <TextField
            type="search"
            aria-label="Wikiの記事名を絞り込む"
            placeholder="記事名を絞り込む"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <SelectField
            aria-label="Wikiの分類"
            value={namespaceFilter}
            onChange={(e) => setNamespaceFilter(e.target.value)}
          >
            <option value="">すべての分類</option>
            {catalog.data?.namespaces.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </SelectField>
          {catalog.isPending && !hidden && <p>記事一覧を読み込み中…</p>}
          {!catalog.isPending && !articles.length && (
            <p>記事がありません。Markdownファイルを取り込んでください。</p>
          )}
          <WikiTree
            articles={articles}
            currentId={route.source || undefined}
            expandAll={!!filter || !!namespaceFilter}
            onSelect={(id) => navigate({ ...route, source: id, section: null })}
          />
        </aside>
        <div className="wiki-reader">
          {!route.source && (
            <div className="wiki-empty">
              <h2>Markdownで知識をまとめる</h2>
              <p>
                記事を選ぶと本文と目次を表示します。記事と資料は「本文を検索」からまとめて検索できます。
              </p>
              <p>同じ分類・パスのファイルを取り込むと本文を更新します。</p>
            </div>
          )}
          {route.source && article.isPending && (
            <StatusMessage>記事を読み込み中…</StatusMessage>
          )}
          {route.source && article.error && (
            <StatusMessage>{article.error.message}</StatusMessage>
          )}
          {route.source && article.data && (
            <>
              <div className="wiki-article-bar">
                <span>
                  {article.data.namespace} / {article.data.path}
                </span>
                {article.data.translation_group && (
                  <SelectField
                    aria-label="記事の言語"
                    value={route.source || ""}
                    onChange={(e) =>
                      navigate({
                        ...route,
                        source: e.target.value,
                        section: null,
                      })
                    }
                  >
                    {(catalog.data?.articles || [])
                      .filter(
                        (a) =>
                          a.namespace === article.data?.namespace &&
                          a.translation_group ===
                            article.data?.translation_group,
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
                {article.data.related_document && (
                  <Button
                    onClick={() =>
                      navigate({
                        ...route,
                        mode: "library",
                        job: article.data?.related_document?.job_id || null,
                        view: "preview",
                        unit: article.data?.source_unit || 1,
                      })
                    }
                  >
                    関連資料：{article.data.related_document.title}
                  </Button>
                )}
                <a
                  href={withBase(
                    `/api/wiki/sources/${encodeURIComponent(route.source)}/markdown`,
                  )}
                >
                  {article.data.entry_kind === "csv"
                    ? "CSVを保存"
                    : "Markdownを保存"}
                </a>
                <Button
                  onClick={() => {
                    deleteTarget.current = route.source;
                    setDeleteOpen(true);
                  }}
                >
                  記事を削除
                </Button>
              </div>
              {article.data.fallback_original && (
                <StatusMessage>
                  日本語訳は未公開です。原文を表示しています。
                </StatusMessage>
              )}
              <div className="wiki-reading-layout">
                <nav className="wiki-outline" aria-label="記事の目次">
                  <strong>目次</strong>
                  <ul>
                    {article.data.outline.map((h) => (
                      <li key={h.anchor} data-level={h.level}>
                        <a
                          href={`/?mode=wiki&source=${encodeURIComponent(route.source || "")}&section=${encodeURIComponent(h.anchor)}`}
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
                  ref={body}
                  className="wiki-body"
                  onClick={(e) => {
                    const anchor = (
                      e.target as HTMLElement
                    ).closest<HTMLAnchorElement>("a");
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
                      setMessage(anchor.title || "リンク先を参照できません。");
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
                    navigate({
                      ...route,
                      mode:
                        url.searchParams.get("mode") === "wiki"
                          ? "wiki"
                          : "library",
                      source: url.searchParams.get("source"),
                      section: url.searchParams.get("section"),
                      job: url.searchParams.get("job"),
                      view: "preview",
                      unit: Number(url.searchParams.get("unit")) || null,
                    });
                  }}
                  dangerouslySetInnerHTML={{ __html: article.data.html }}
                />
              </div>
            </>
          )}
        </div>
      </div>
      <Dialog
        id="wikiImport"
        className="library-dialog"
        aria-labelledby="wikiImportTitle"
        open={importOpen}
        busy={busy}
        onClose={() => setImportOpen(false)}
      >
        <DialogHeading
          id="wikiImportTitle"
          title="Markdown記事を取り込む"
          onClose={() => setImportOpen(false)}
          disabled={busy}
        />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void importFiles();
          }}
        >
          <label>
            分類名
            <TextField
              value={namespace}
              required
              maxLength={100}
              onChange={(e) => setNamespace(e.target.value)}
            />
          </label>
          <p>
            UTF-8の .md / .markdown 記事と .csv 目次を取り込めます。
            wiki-manifest.json または pages.jsonl
            を一緒に選ぶと言語・翻訳状態も取り込みます。
            同じ分類・パスの記事は更新されます。
          </p>
          <label>
            ファイルを選択
            <input
              aria-label="WikiのMarkdownファイル"
              type="file"
              accept=".md,.markdown,.csv,.json,.jsonl"
              multiple
              onChange={(e) => setFiles([...(e.target.files || [])])}
            />
          </label>
          <label>
            フォルダーを選択
            <input
              aria-label="Wikiのフォルダー"
              type="file"
              multiple
              ref={(node) => {
                node?.setAttribute("webkitdirectory", "");
              }}
              onChange={(e) =>
                setFiles(
                  [...(e.target.files || [])].filter(
                    (f) =>
                      /\.(md|markdown|csv)$/i.test(f.name) ||
                      ["wiki-manifest.json", "pages.jsonl"].includes(f.name),
                  ),
                )
              }
            />
          </label>
          <p>{files.length}件を選択</p>
          <StatusMessage>{message}</StatusMessage>
          <DialogActions>
            <Button disabled={busy} onClick={() => setImportOpen(false)}>
              閉じる
            </Button>
            <Button
              type="submit"
              className="primary"
              disabled={
                busy || !files.some((f) => /\.(md|markdown|csv)$/i.test(f.name))
              }
            >
              {busy ? "取り込み中…" : "取り込む"}
            </Button>
          </DialogActions>
        </form>
      </Dialog>
      <Dialog
        className="library-dialog"
        aria-labelledby="wikiDeleteTitle"
        open={deleteOpen}
        busy={busy}
        onClose={() => setDeleteOpen(false)}
      >
        <DialogHeading
          id="wikiDeleteTitle"
          title="記事を削除"
          onClose={() => setDeleteOpen(false)}
          disabled={busy}
        />
        <p>この記事をWikiと検索対象から削除します。</p>
        <StatusMessage>{message}</StatusMessage>
        <DialogActions>
          <Button disabled={busy} onClick={() => setDeleteOpen(false)}>
            閉じる
          </Button>
          <Button disabled={busy} onClick={() => void deleteArticle()}>
            削除する
          </Button>
        </DialogActions>
      </Dialog>
    </section>
  );
}

export function KnowledgeSearch({
  open,
  onClose,
  route,
  navigate,
  initialAction = "search",
}: {
  initialAction?: "search" | "rag";
  open: boolean;
  onClose: () => void;
  route: Route;
  navigate: (route: Route) => void;
}) {
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState("text"),
    [kind, setKind] = useState("all"),
    [scope, setScope] = useState("all"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [retrieval, setRetrieval] = useState<Task | null>(null),
    [operation, setOperation] = useState<"search" | "rag">(initialAction);
  const cache = useQueryClient();
  const action = useAsyncScope(
    `${open}:${route.mode}:${route.job}:${route.source}:${route.folder}`,
  );
  const inFlight = useRef(false);
  const currentSource =
    route.mode === "wiki"
      ? route.source
      : route.job
        ? `doc-${route.job}`
        : null;
  const hasFolder = route.mode === "library" && !!route.folder;
  const effectiveScope =
    (scope === "current" && !currentSource) ||
    (scope === "folder" && !hasFolder)
      ? "all"
      : scope;
  useEffect(() => {
    setScope(effectiveScope);
  }, [effectiveScope]);
  useEffect(() => {
    inFlight.current = false;
    setBusy(false);
    setError("");
    setRetrieval(null);
    setOperation(initialAction);
  }, [open, route.mode, route.job, route.source, route.folder, initialAction]);
  function close() {
    action.invalidate();
    onClose();
  }
  const status = useQuery({
    queryKey: ["knowledge-status"],
    enabled: open,
    queryFn: ({ signal }) =>
      request<IndexStatus>("/api/knowledge/status", { signal }),
    refetchInterval: 2000,
  });
  const task = useQuery({
    queryKey: ["knowledge-search", retrieval?.id],
    enabled: open && !!retrieval,
    queryFn: ({ signal }) =>
      request<Task>(`/api/knowledge/retrievals/${retrieval?.id}`, { signal }),
    refetchInterval: (q) =>
      q.state.data && ["failed", "cancelled"].includes(q.state.data.state)
        ? false
        : 2000,
  });
  const current = task.data?.id === retrieval?.id ? task.data : retrieval;
  useEffect(() => {
    if (status.data && !status.data.azure_configured) setMode("text");
  }, [status.data]);
  async function search(nextOperation: "search" | "rag" = "search") {
    if (
      !query.trim() ||
      inFlight.current ||
      (current && ["queued", "running"].includes(current.state))
    )
      return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setOperation(nextOperation);
    setRetrieval(null);
    const isCurrent = action.start();
    try {
      const value = await post<Task>(
        nextOperation === "rag"
          ? "/api/knowledge/answers"
          : "/api/knowledge/search",
        {
          query,
          mode,
          kind,
          source_id: effectiveScope === "current" ? currentSource : null,
          folder_id: effectiveScope === "folder" ? route.folder : null,
          client_request_id: crypto.randomUUID(),
        },
      );
      if (isCurrent()) setRetrieval(value);
    } catch (e) {
      if (isCurrent()) setError(errorText(e));
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }
  async function index() {
    if (inFlight.current) return;
    inFlight.current = true;
    const isCurrent = action.start();
    setBusy(true);
    setError("");
    try {
      await post("/api/knowledge/index-jobs", {});
      await status.refetch();
    } catch (e) {
      if (isCurrent()) setError(errorText(e));
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }
  async function cancelTask(id: string, searchTask: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    const isCurrent = action.start();
    setBusy(true);
    try {
      const value = await post<Task>(`/api/knowledge/tasks/${id}/cancel`, {});
      if (searchTask) {
        await cache.cancelQueries({ queryKey: ["knowledge-search", id] });
        cache.setQueryData(["knowledge-search", id], value);
        if (isCurrent()) setRetrieval(value);
      } else await status.refetch();
    } catch (e) {
      if (isCurrent()) setError(errorText(e));
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  }
  function hitRoute(hit: Hit): Route {
    return hit.kind === "wiki"
      ? {
          ...route,
          mode: "wiki",
          source: hit.source_id,
          section: hit.locator.anchor || null,
        }
      : {
          ...route,
          mode: "library",
          job: hit.job_id,
          view: "preview",
          unit: hit.unit,
        };
  }
  function hitUrl(hit: Hit) {
    const target = hitRoute(hit);
    const params = new URLSearchParams({ mode: target.mode });
    if (target.mode === "wiki") {
      params.set("source", target.source!);
      if (target.section) params.set("section", target.section);
    } else {
      params.set("job", target.job!);
      params.set("view", "preview");
      params.set("unit", String(target.unit));
    }
    return withBase(`/?${params}`);
  }
  function openHit(e: React.MouseEvent<HTMLAnchorElement>, hit: Hit) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0)
      return;
    e.preventDefault();
    close();
    navigate(hitRoute(hit));
  }
  const waiting = current && ["queued", "running"].includes(current.state),
    indexJob = status.data?.jobs.find((j) =>
      ["queued", "running"].includes(j.state),
    );
  return (
    <Dialog
      id="knowledgeSearch"
      className="knowledge-dialog"
      aria-labelledby="knowledgeSearchTitle"
      open={open}
      onClose={close}
    >
      <DialogHeading
        id="knowledgeSearchTitle"
        title={operation === "rag" ? "RAGで質問" : "本文検索"}
        onClose={close}
      />
      <form
        className="knowledge-search-form"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <Icon name="search" />
        <TextField
          type="search"
          required
          maxLength={4000}
          value={query}
          placeholder="検索キーワードや質問を入力"
          aria-label="検索文"
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
        <Button type="submit" className="primary" disabled={busy || !!waiting}>
          {busy && operation === "search" ? "検索を開始…" : "検索"}
        </Button>
        <Button
          className="knowledge-rag-button"
          disabled={busy || !!waiting || !query.trim()}
          onClick={() => void search("rag")}
        >
          {busy && operation === "rag" ? "RAGを開始…" : "RAG"}
        </Button>
        <p className="knowledge-search-help">
          RAGは検索上位5件から関連する本文を選び、キーワードの解説と出典リンク付きで回答します。
        </p>
        <div className="knowledge-controls">
          <label>
            検索方法
            <SelectField value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="text">全文検索</option>
              <option
                value="semantic"
                disabled={!status.data?.azure_configured}
              >
                意味検索
              </option>
              <option value="hybrid" disabled={!status.data?.azure_configured}>
                全文＋意味検索
              </option>
            </SelectField>
          </label>
          <label>
            検索対象
            <SelectField value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="all">両方</option>
              <option value="document">資料</option>
              <option value="wiki">Wiki</option>
            </SelectField>
          </label>
          <label>
            範囲
            <SelectField
              value={effectiveScope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="all">すべて</option>
              <option
                value="current"
                disabled={!(route.mode === "wiki" ? route.source : route.job)}
              >
                現在の資料・記事
              </option>
              <option
                value="folder"
                disabled={route.mode !== "library" || !route.folder}
              >
                現在のフォルダー配下
              </option>
            </SelectField>
          </label>
        </div>
      </form>
      <div className="knowledge-index">
        <span>
          意味検索の準備：{status.data?.embedded || 0} /{" "}
          {status.data?.chunks || 0}断片
        </span>
        {status.data?.azure_configured ? (
          <>
            <Button disabled={busy || !!indexJob} onClick={() => void index()}>
              意味検索の索引を作成・更新
            </Button>
            <small>Azureへの通信後は15秒待ちます。</small>
          </>
        ) : (
          <small>
            {status.data?.configuration_error ||
              "Azure未設定。全文検索を利用できます。"}
          </small>
        )}
        {indexJob && (
          <>
            <span>
              索引作成中 {indexJob.progress} / {indexJob.total}
            </span>
            <Button
              disabled={busy}
              onClick={() => void cancelTask(indexJob.id, false)}
            >
              索引作成を中止
            </Button>
          </>
        )}
        {!indexJob && status.data?.jobs[0]?.state === "failed" && (
          <StatusMessage>{status.data.jobs[0].error}</StatusMessage>
        )}
      </div>
      <StatusMessage>
        {error ||
          task.error?.message ||
          status.error?.message ||
          current?.error ||
          current?.output.notice ||
          ""}
      </StatusMessage>
      {waiting && (
        <StatusMessage>
          {operation === "rag"
            ? "関連する本文を選び、回答を作成しています…"
            : "検索を処理中です。Azureの待ち時間も含みます。"}
          <Button
            disabled={busy}
            onClick={() => void cancelTask(current.id, true)}
          >
            {operation === "rag" ? "RAGを中止" : "検索を中止"}
          </Button>
        </StatusMessage>
      )}
      {current?.output.stale && (
        <StatusMessage>
          出典が更新されました。検索をやり直してください。
        </StatusMessage>
      )}
      {current?.state === "completed" &&
        !current.output.results?.length &&
        !current.output.answer && <p>該当する本文はありません。</p>}
      {current?.output.answer && !current.output.stale && (
        <section className="knowledge-answer" aria-label="RAGの回答">
          <h3>回答</h3>
          <div className="knowledge-answer-text">
            {current.output.answer.split(/(\[S\d+\])/g).map((part, i) => {
              const citation = current.output.citations?.find(
                (c) => `[${c.label}]` === part,
              );
              return citation ? (
                <a
                  key={i}
                  href={hitUrl(citation)}
                  onClick={(e) => openHit(e, citation)}
                  aria-label={`${part} ${citation.title}の本文を開く`}
                >
                  {part}
                </a>
              ) : (
                part
              );
            })}
          </div>
          {!!current.output.unknowns?.length && (
            <div className="knowledge-answer-unknowns">
              <h4>本文から確認できないこと</h4>
              <ul>
                {current.output.unknowns.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      {!!current?.output.results?.length && (
        <p className="knowledge-result-count">
          {operation === "rag" ? "採用した本文" : "検索結果"} ·{" "}
          {current.output.results.length}件
        </p>
      )}
      <ul className="knowledge-results">
        {current?.output.results?.map((hit) => (
          <li key={hit.chunk_id}>
            <div className="knowledge-result-source">
              <span>{hit.kind === "wiki" ? "Wiki" : "資料"}</span>
              {hit.locator.heading && <span> › {hit.locator.heading}</span>}
              {hit.kind === "document" && <span> · 表示位置 {hit.unit}</span>}
              {hit.partial && <span> · 一部抽出</span>}
            </div>
            <a
              className="knowledge-result-title"
              href={hitUrl(hit)}
              aria-label={`${hit.kind === "wiki" ? "Wiki" : "資料"} · ${hit.title}`}
              onClick={(e) => openHit(e, hit)}
            >
              {hit.title}
            </a>
            <p className="knowledge-result-snippet">
              {searchSnippet(hit.text, query)}
            </p>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

function searchSnippet(text: string, query: string) {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= 300) return compact;
  const term = query
    .trim()
    .split(/\s+/)
    .find((word) => compact.toLowerCase().includes(word.toLowerCase()));
  const match = term ? compact.toLowerCase().indexOf(term.toLowerCase()) : 0;
  const start = Math.max(0, match - 80);
  return `${start ? "…" : ""}${compact.slice(start, start + 300)}${start + 300 < compact.length ? "…" : ""}`;
}
