import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { FeatureBoundary, lazyFeature } from "../../components/FeatureBoundary";
import { IconButton, StatusMessage, Toolbar } from "../../components/ui";
import { Icon } from "../../components/Icons";
import { isWikiArticle, isWikiImportFile } from "../../lib/formats";
import type { Route, SourceTarget } from "../../lib/route";
import { newWikiDraft, type WikiDraft } from "../wiki-draft";
import { isHttpError } from "../../lib/http";
import { useArticleReveal } from "./useArticleReveal";
import { useWikiActions } from "./useWikiActions";
import {
  isArticleGone,
  useWikiArticle,
  useWikiCatalog,
} from "./useWikiQueries";
import { WikiArticleView } from "./WikiArticleView";
import { WikiCatalog } from "./WikiCatalog";
import { WikiDeleteDialog, WikiImportDialog } from "./WikiDialogs";
import "../knowledge.css";

// The editing form (and the Markdown parser behind it) is fetched when a draft
// is opened; reading and searching the Wiki do not need it.
const WikiEditor = lazyFeature(() =>
  import("../WikiEditor").then((m) => ({ default: m.WikiEditor })),
);

/**
 * The Wiki screen: catalog, the open article or the editor, and the import /
 * delete dialogs. Reads come from `useWikiQueries`, changing requests from
 * `useWikiActions`; what is kept here is only what the person is doing (the
 * filters, the draft being edited, which dialog is open, the files chosen).
 * The shared navigation is handed in as `navigation`.
 */
export function Wiki({
  route,
  navigate,
  navigation,
  hidden,
  onReference,
}: {
  route: Route;
  navigate: (route: Route) => void;
  navigation: ReactNode;
  hidden: boolean;
  /** A link to another source was followed (see `app/return-point`). */
  onReference: (target: SourceTarget) => void;
}) {
  const catalog = useWikiCatalog(hidden);
  const article = useWikiArticle(route.source, hidden);
  const actions = useWikiActions({ hidden, route, navigate });
  const { busy, message, setMessage } = actions;
  const [filter, setFilter] = useState(""),
    [namespaceFilter, setNamespaceFilter] = useState("");
  const [importOpen, setImportOpen] = useState(false),
    [files, setFiles] = useState<File[]>([]),
    [deleteOpen, setDeleteOpen] = useState(false);
  const [editor, setEditor] = useState<WikiDraft | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const deleteTarget = useRef<string | null>(null);
  useEffect(() => {
    setImportOpen(false);
    setDeleteOpen(false);
  }, [hidden, route.source]);
  useArticleReveal(body, {
    hidden,
    title: article.data?.title,
    revision: article.data?.revision,
    source: route.source,
    section: route.section,
  });
  return (
    <section
      className="wiki-screen"
      hidden={hidden}
      aria-label="Wiki"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDrop={(e) => {
        // Plate's HTML5 backend receives the drop at window bubble phase.
        // Let it own editor drops rather than treating them as article imports.
        if (
          editor &&
          e.target instanceof Element &&
          e.target.closest(".wiki-plate")
        )
          return;
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.stopPropagation();
        if (editor) {
          setMessage("記事の編集を閉じてから、Markdownを取り込んでください。");
          return;
        }
        if (busy) {
          setMessage("取り込み中です。完了後に追加してください。");
          return;
        }
        const dropped = [...e.dataTransfer.files];
        const accepted = dropped.filter((f) => isWikiImportFile(f.name));
        if (!accepted.some((f) => isWikiArticle(f.name))) {
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
        <div className="wiki-actions">
          <IconButton
            icon="upload"
            label="Markdownを取り込む"
            disabled={busy || !!editor}
            onClick={() => {
              setMessage("");
              setImportOpen(true);
            }}
          />
          <IconButton
            icon="file-plus"
            label="新しい記事を作成"
            className="primary"
            disabled={busy || !!editor}
            onClick={() => {
              setMessage("");
              setEditor(newWikiDraft(namespaceFilter || "Wiki"));
            }}
          />
        </div>
      </Toolbar>
      <StatusMessage
        className="wiki-message"
        kind={!message && catalog.error ? "error" : undefined}
      >
        {message || catalog.error?.message || ""}
      </StatusMessage>
      <div className="wiki-layout">
        <WikiCatalog
          articles={catalog.data?.articles || []}
          namespaces={catalog.data?.namespaces || []}
          pending={catalog.isPending && !hidden}
          filter={filter}
          namespaceFilter={namespaceFilter}
          currentId={route.source || undefined}
          onFilter={setFilter}
          onNamespaceFilter={setNamespaceFilter}
          onSelect={(id) => {
            if (editor)
              setMessage(
                "編集中の記事を保存するか、編集を閉じてから選択してください。",
              );
            else navigate({ ...route, source: id, section: null });
          }}
        />
        <div className="wiki-reader">
          {editor ? (
            <FeatureBoundary
              onRetry={WikiEditor.reset}
              fallback={(retry) => (
                <>
                  <StatusMessage
                    kind="error"
                    recovery={[
                      { label: "もう一度読み込む", onClick: retry },
                      { label: "閉じる", onClick: () => setEditor(null) },
                    ]}
                  >
                    編集画面を読み込めませんでした。保存済みの記事は変わりません。
                  </StatusMessage>
                </>
              )}
            >
              <Suspense
                fallback={
                  <StatusMessage kind="loading">
                    編集画面を読み込み中…
                  </StatusMessage>
                }
              >
                <WikiEditor
                  key={editor.id}
                  initial={editor}
                  namespaces={catalog.data?.namespaces || []}
                  onClose={() => setEditor(null)}
                  onSaved={(saved) => {
                    setEditor(null);
                    actions.saved(saved);
                  }}
                />
              </Suspense>
            </FeatureBoundary>
          ) : (
            <>
              {!route.source && (
                <div className="wiki-empty">
                  <h2>Markdownで知識をまとめる</h2>
                  <p>
                    記事を選ぶと本文と目次を表示します。記事と資料は「本文を検索」からまとめて検索できます。
                  </p>
                  <p>同じ分類・パスのファイルを取り込むと本文を更新します。</p>
                  <p>
                    右上の新規作成ボタンから、記事を書いてWikiに保存できます。
                  </p>
                </div>
              )}
              {route.source && article.isPending && (
                <StatusMessage kind="loading">記事を読み込み中…</StatusMessage>
              )}
              {route.source && article.error && (
                <ArticleUnavailable
                  error={article.error}
                  onReload={() => void article.refetch()}
                />
              )}
              {route.source &&
                article.data &&
                !isArticleGone(article.error) && (
                  <WikiArticleView
                    article={article.data}
                    siblings={catalog.data?.articles || []}
                    route={route}
                    source={route.source}
                    navigate={navigate}
                    onReference={onReference}
                    busy={busy}
                    bodyRef={body}
                    onEdit={() => void actions.loadDraft(setEditor)}
                    onDelete={() => {
                      deleteTarget.current = route.source;
                      setDeleteOpen(true);
                    }}
                    onNotice={setMessage}
                  />
                )}
            </>
          )}
        </div>
      </div>
      <WikiImportDialog
        open={importOpen}
        busy={busy}
        message={message}
        files={files}
        onFiles={setFiles}
        onClose={() => setImportOpen(false)}
        onImport={(namespace) =>
          void actions.importFiles(files, namespace, () => {
            setImportOpen(false);
            setFiles([]);
          })
        }
      />
      <WikiDeleteDialog
        open={deleteOpen}
        busy={busy}
        message={message}
        title={article.data?.title}
        onClose={() => setDeleteOpen(false)}
        onDelete={() =>
          void actions.deleteArticle(deleteTarget.current, () =>
            setDeleteOpen(false),
          )
        }
      />
    </section>
  );
}

/**
 * An article that could not be opened. A deleted or replaced article and one
 * the person may not read are named as such and are not retried: reloading
 * cannot bring them back, and no other article is shown in their place.
 */
function ArticleUnavailable({
  error,
  onReload,
}: {
  error: Error;
  onReload: () => void;
}) {
  if (isArticleGone(error))
    return (
      <StatusMessage
        kind="stale"
        detail="削除されたか、更新で別の記事に置き換わった可能性があります。記事一覧から選び直してください。別の記事を代わりに表示することはしません。"
      >
        この記事は見つかりません。
      </StatusMessage>
    );
  if (isHttpError(error, 403) || isHttpError(error, 401))
    return (
      <StatusMessage
        kind="error"
        detail="閲覧できる利用者かどうかは、管理者に確認してください。"
      >
        この記事を閲覧する権限がありません。
      </StatusMessage>
    );
  return (
    <StatusMessage
      kind="error"
      recovery={[{ label: "再読み込み", onClick: onReload }]}
    >
      {error.message}
    </StatusMessage>
  );
}
