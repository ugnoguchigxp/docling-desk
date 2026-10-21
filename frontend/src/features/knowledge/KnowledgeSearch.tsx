import { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogHeading,
  SelectField,
  StatusMessage,
  TextField,
} from "../../components/ui";
import { Icon } from "../../components/Icons";
import type { Route, SourceTarget } from "../../lib/route";
import { hitTarget } from "./hit";
import { IndexPanel } from "./IndexPanel";
import { SearchResults } from "./SearchResults";
import { resolveScope, useKnowledgeRetrieval } from "./useKnowledgeRetrieval";
import "../knowledge.css";

/**
 * The search / RAG dialog. It keeps what the person has typed and chosen (the
 * search text, method, target, scope); the server work and its result belong
 * to `useKnowledgeRetrieval`, and the result display to `SearchResults`.
 *
 * Opening a result closes the dialog and hands the source to `onReference`
 * (which navigates). What was typed, chosen and shown stays in this component,
 * and the last opened link and the list's scroll position are remembered, so a
 * `resume` open (the person came back from the source) shows the same results
 * with focus on the link they followed. A plain open starts a new search.
 */
export function KnowledgeSearch({
  open,
  onClose,
  route,
  initialAction = "search",
  resume = false,
  onReference,
}: {
  initialAction?: "search" | "rag";
  /** Open on the last results, focused on the link that was followed. */
  resume?: boolean;
  /** A result was chosen: open its source (and remember how to come back). */
  onReference: (target: SourceTarget) => void;
  open: boolean;
  onClose: () => void;
  route: Route;
}) {
  const [query, setQuery] = useState(""),
    [mode, setMode] = useState("text"),
    [kind, setKind] = useState("all"),
    [scope, setScope] = useState("all");
  const retrieval = useKnowledgeRetrieval({
    open,
    route,
    initialAction,
    resume,
  });
  const followed = useRef<{ key: string; scrollTop: number } | null>(null),
    [openedKey, setOpenedKey] = useState<string | null>(null);
  const { status, current, waiting, operation, busy } = retrieval;
  const effectiveScope = resolveScope(route, scope).effective;
  useEffect(() => {
    setScope(effectiveScope);
  }, [effectiveScope]);
  useEffect(() => {
    if (status.data && !status.data.azure_configured) setMode("text");
  }, [status.data]);
  useEffect(() => {
    if (!open) return;
    if (!resume) {
      followed.current = null;
      setOpenedKey(null);
      return;
    }
    // The dialog is already modal here; put the person back where they were.
    const dialog = document.getElementById("knowledgeSearch");
    const target = followed.current;
    if (!dialog || !target) return;
    dialog.scrollTop = target.scrollTop;
    const link = [
      ...dialog.querySelectorAll<HTMLElement>("[data-hit-key]"),
    ].find((node) => node.dataset.hitKey === target.key);
    link?.focus({ preventScroll: true });
  }, [open, resume]);
  function close() {
    retrieval.stop();
    onClose();
  }
  const input = { query, mode, kind, scope: effectiveScope };
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
          void retrieval.search(input);
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
        <Button type="submit" className="primary" disabled={busy || waiting}>
          {busy && operation === "search" ? "検索を開始…" : "検索"}
        </Button>
        <Button
          className="knowledge-rag-button"
          disabled={busy || waiting || !query.trim()}
          onClick={() => void retrieval.search(input, "rag")}
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
      <IndexPanel
        status={status.data}
        indexJob={retrieval.indexJob}
        busy={busy}
        onIndex={() => void retrieval.index()}
        onCancel={(id) => void retrieval.cancel(id, "index")}
      />
      <StatusMessage
        kind={
          retrieval.error ||
          retrieval.taskError ||
          status.error ||
          current?.error
            ? "error"
            : undefined
        }
      >
        {retrieval.error ||
          retrieval.taskError?.message ||
          status.error?.message ||
          current?.error ||
          current?.output.notice ||
          ""}
      </StatusMessage>
      <SearchResults
        current={current}
        waiting={waiting}
        operation={operation}
        query={query}
        busy={busy}
        openedKey={openedKey}
        onCancel={(id) => void retrieval.cancel(id, "retrieval")}
        onOpen={(hit, key) => {
          followed.current = {
            key,
            scrollTop:
              document.getElementById("knowledgeSearch")?.scrollTop ?? 0,
          };
          setOpenedKey(key);
          close();
          onReference(hitTarget(hit));
        }}
      />
    </Dialog>
  );
}
