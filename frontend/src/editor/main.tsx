import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { RawEditor, sourceEdit } from "./RawEditor";
import { AppErrorBoundary } from "../components/AppErrorBoundary";
import "../components/app-tokens.css";
import "./style.css";

type Metadata = {
  title: string;
  collection_id: string;
  project_id?: string;
  region?: string;
  source_kind: "document" | "wiki";
  language: string;
};
type Draft = {
  draft_id: string;
  source_id: string;
  base_revision: string | null;
  base_etag: string | null;
  body: string;
  metadata: Metadata;
  etag: string;
  sha256: string;
  version: number;
  state: "draft" | "committed";
  attachments: string[];
};
type Document = {
  source_id: string;
  source_revision: string;
  etag: string;
  body?: string;
  content_kind: "original_markdown" | "extracted_text";
  metadata: Metadata;
  status: {
    search: string;
    fts_ready: boolean;
    semantic_ready: boolean;
    semantic_configured: boolean;
    error_code: string | null;
    evidence_revision: string | null;
  };
};
class RequestError extends Error {
  constructor(
    readonly status: number,
    code: string,
  ) {
    super(code);
  }
}
async function request(path: string, body?: unknown) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  // A gateway error (502 HTML page, empty body) is not JSON: report the status.
  const text = await response.text();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let data: any = null;
  try {
    data = JSON.parse(text);
  } catch {
    /* handled below */
  }
  if (!response.ok) {
    const detail = data?.detail ?? data?.error?.code;
    throw new RequestError(
      response.status,
      typeof detail === "string"
        ? detail
        : data === null
          ? `サーバーから正しい応答がありませんでした (HTTP ${response.status})`
          : "request_failed",
    );
  }
  if (data === null)
    throw new RequestError(
      response.status,
      "サーバーの応答を読み込めませんでした。",
    );
  return data;
}
const POLL_MS = 2000;
const SESSION_LOST = [401, 403, 404, 410];
const savedState = (data: Document) =>
  "保存版確定済み · " +
  (data.status.search === "failed"
    ? "全文検索反映失敗"
    : data.status.fts_ready
      ? "全文検索反映完了"
      : "検索反映待ち") +
  " · " +
  (!data.status.semantic_configured
    ? "意味検索未設定"
    : data.status.semantic_ready
      ? "意味検索反映完了"
      : data.status.error_code
        ? "意味検索反映失敗"
        : "意味検索反映待ち");
/** True once the index state can no longer change by itself. */
const indexSettled = (data: Document) =>
  data.status.search === "failed" ||
  (data.status.fts_ready &&
    (!data.status.semantic_configured ||
      data.status.semantic_ready ||
      !!data.status.error_code));
function target() {
  const query = new URLSearchParams(location.search);
  if (query.get("draft_id")) return { draft_id: query.get("draft_id") };
  if (query.get("source_id")) return { source_id: query.get("source_id") };
  try {
    return {
      scope: JSON.parse(query.get("scope") ?? '{"collection_id":"documents"}'),
    };
  } catch {
    throw new Error("編集対象が不正です");
  }
}
export function Editor() {
  const [code, setCode] = useState(""),
    [session, setSession] = useState(""),
    [error, setError] = useState(""),
    [status, setStatus] = useState(""),
    [draft, setDraft] = useState<Draft | null>(null),
    [body, setBody] = useState(""),
    [title, setTitle] = useState(""),
    [documents, setDocuments] = useState<Document[]>([]),
    [scope, setScope] = useState<Metadata | null>(null),
    [busy, setBusy] = useState(false),
    [preparing, setPreparing] = useState(false),
    [mode, setMode] = useState<"rich" | "source">("rich"),
    [generation, setGeneration] = useState(0),
    [savedSource, setSavedSource] = useState<Document | null>(null),
    [backup, setBackup] = useState(""),
    [expired, setExpired] = useState(false);
  const pending = useRef<{
    name: string;
    arguments: Record<string, unknown>;
  } | null>(null);
  const writing = useRef(false);
  const bodyRef = useRef(body);
  const dirtyRef = useRef(false);
  bodyRef.current = body;
  const call = useCallback(
    (name: string, args: Record<string, unknown>) =>
      request(`/editor/session/${session}/tools`, { name, arguments: args }),
    [session],
  );
  const showDraft = useCallback((d: Draft) => {
    if (typeof d.body !== "string" || typeof d.etag !== "string")
      throw new Error("invalid_response");
    setDraft(d);
    setBody(d.body);
    setTitle(d.metadata.title);
    setGeneration((v) => v + 1);
    setError("");
    setStatus(d.state === "draft" ? "下書き保存済み" : "保存版確定済み");
  }, []);
  useEffect(() => {
    if (session) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    void request("/editor/challenges", target())
      .then((challenge) => {
        if (stopped) return;
        setCode(challenge.code);
        const poll = async () => {
          try {
            const data = await request(
              `/editor/challenges/${challenge.challenge}`,
            );
            if (stopped) return;
            if (data.session) {
              setCode("");
              setSession(data.session);
            } else timer = setTimeout(() => void poll(), 1500);
          } catch (e) {
            if (!stopped) setError(String(e));
          }
        };
        void poll();
      })
      .catch((e) => {
        if (!stopped) setError(String(e));
      });
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [session]);
  useEffect(() => {
    if (!session) return;
    let stopped = false;
    void request(`/editor/session/${session}`)
      .then(async (state) => {
        if (stopped) return;
        setScope({
          title: "",
          language: "ja",
          source_kind: "wiki",
          ...state.scope,
        });
        if (state.draft_id)
          showDraft(
            await call("get_document_draft", { draft_id: state.draft_id }),
          );
        else if (state.source_id) {
          const source = await call("get_document", {
            source_id: state.source_id,
          });
          showDraft(
            await call("create_document_draft", {
              source_id: source.source_id,
              source_revision: source.source_revision,
              source_etag: source.etag,
              idempotency_key: crypto.randomUUID(),
            }),
          );
        } else {
          const data = await call("list_documents", {});
          setDocuments(data.documents);
        }
      })
      .catch((e) => {
        if (!stopped) setError(String(e));
      });
    const timer = setInterval(() => {
      void request(`/editor/session/${session}`).catch((e) => {
        if (!stopped) {
          setError("接続が失効しました。再接続が必要です: " + String(e));
          if (e instanceof RequestError && SESSION_LOST.includes(e.status)) {
            // Never discard what the user typed: keep it on screen (read-only)
            // and in the copy/download area until they reconnect.
            setExpired(true);
            if (dirtyRef.current)
              setBackup((previous) => previous || bodyRef.current);
          }
        }
      });
    }, 15000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [session, call, showDraft]);
  const dirty =
    !!draft && (body !== draft.body || title !== draft.metadata.title);
  dirtyRef.current = dirty;
  const working = busy || preparing || expired;
  const unsaved = dirty || !!backup;
  useEffect(() => {
    if (!unsaved) return;
    const before = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, [unsaved]);
  const awaitingCommit = pending.current?.name === "commit_document_draft";
  const write = useCallback(
    async (name: string, args: Record<string, unknown>, retry = false) => {
      if (writing.current || (pending.current && !retry))
        throw new Error("未確認の保存結果を先に確認してください。");
      writing.current = true;
      setBusy(true);
      setError("");
      const operation = pending.current ?? {
        name,
        arguments: { ...args, idempotency_key: crypto.randomUUID() },
      };
      pending.current = operation;
      try {
        const result = await call(operation.name, operation.arguments);
        pending.current = null;
        return result;
      } catch (e) {
        setError(
          String(e) + " · 入力は保持されています。同じ操作で再試行できます。",
        );
        throw e;
      } finally {
        writing.current = false;
        setBusy(false);
      }
    },
    [call],
  );
  const save = useCallback(async () => {
    if (!draft) return;
    const result = await write("update_document_draft", {
      draft_id: draft.draft_id,
      body,
      metadata: { ...draft.metadata, title },
      etag: draft.etag,
    });
    if (typeof result.body === "string") {
      setDraft(result);
      setStatus("下書き保存済み");
    } else if (result.source_revision) {
      setDraft({ ...draft, state: "committed" });
      setStatus("保存版確定済み");
      setSavedSource(
        await call("get_document", { source_id: result.source_id }),
      );
    }
  }, [draft, body, title, write, call]);
  useEffect(() => {
    if (!dirty || working || error || draft?.state !== "draft") return;
    const timer = setTimeout(() => void save().catch(() => {}), 900);
    return () => clearTimeout(timer);
  }, [dirty, working, error, draft, save]);
  const create = async (source?: Document) => {
    try {
      const args = source
        ? {
            source_id: source.source_id,
            source_revision: source.source_revision,
            source_etag: source.etag,
          }
        : { body: "", metadata: { ...scope, title: title || "新しい文書" } };
      showDraft(await write("create_document_draft", args));
    } catch {
      /* error is displayed */
    }
  };
  const commit = async () => {
    if (!draft) return;
    setPreparing(true);
    try {
      let d = draft;
      if (dirty) {
        d = await write("update_document_draft", {
          draft_id: d.draft_id,
          body,
          metadata: { ...d.metadata, title },
          etag: d.etag,
        });
        showDraft(d);
      }
      const result = await write("commit_document_draft", {
        draft_id: d.draft_id,
        etag: d.etag,
        source_etag: d.base_etag,
      });
      setDraft({ ...d, state: "committed" });
      setStatus("保存版確定済み · 検索反映待ち");
      const source = await call("get_document", {
        source_id: result.source_id,
      });
      setSavedSource(source);
      setDraft(await call("get_document_draft", { draft_id: d.draft_id }));
    } catch {
      /* error is displayed */
    } finally {
      setPreparing(false);
    }
  };
  const savedSourceId = savedSource?.source_id;
  const savedRevision = savedSource?.source_revision;
  useEffect(() => {
    if (!savedSourceId || !session || expired) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    // One request at a time: the next one is scheduled only after the previous
    // answer, stops once indexing has settled and skips hidden tabs.
    const tick = async () => {
      if (stopped) return;
      if (document.hidden) {
        timer = setTimeout(() => void tick(), POLL_MS);
        return;
      }
      try {
        const data = await call("get_document", { source_id: savedSourceId });
        if (stopped) return;
        setSavedSource(data);
        setStatus(savedState(data));
        if (indexSettled(data)) return;
      } catch (e) {
        if (stopped) return;
        setError(String(e));
        if (e instanceof RequestError && SESSION_LOST.includes(e.status))
          return;
      }
      timer = setTimeout(() => void tick(), POLL_MS);
    };
    timer = setTimeout(() => void tick(), POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [savedSourceId, savedRevision, session, expired, call]);
  const refresh = async () => {
    if (!draft) return;
    try {
      setBackup(body);
      pending.current = null;
      showDraft(await call("get_document_draft", { draft_id: draft.draft_id }));
    } catch (e) {
      setError(String(e));
    }
  };
  const copyBackup = async () => {
    try {
      await navigator.clipboard.writeText(backup);
      setStatus("退避した入力をコピーしました");
    } catch {
      setStatus("コピーできませんでした。退避した入力を手動で選択してください");
    }
  };
  const downloadBackup = () => {
    const url = URL.createObjectURL(
      new Blob([backup], { type: "text/markdown;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${title.trim().replace(/[\\/:*?"<>|]/g, "_") || "backup"}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const attach = async (file: File) => {
    if (!draft) return;
    setPreparing(true);
    try {
      let d = draft;
      if (dirty) {
        d = await write("update_document_draft", {
          draft_id: d.draft_id,
          body,
          metadata: { ...d.metadata, title },
          etag: d.etag,
        });
        setDraft(d);
      }
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (bytes.length > 1024 * 1024)
        throw new Error(
          "画面からの添付は1 MiB以下です。大きいファイルはHTTP APIで登録してください。",
        );
      let binary = "";
      for (const byte of bytes) binary += String.fromCharCode(byte);
      const result = await write("add_document_attachment", {
        draft_id: d.draft_id,
        etag: d.etag,
        filename: file.name,
        media_type: file.type || "application/octet-stream",
        body_base64: btoa(binary),
      });
      const current = {
        ...d,
        etag: result.etag,
        version: d.version + 1,
        attachments: [...d.attachments, result.attachment_id],
      };
      showDraft({
        ...current,
        body:
          current.body +
          "\n![" +
          file.name.replaceAll("[", "").replaceAll("]", "") +
          "](" +
          result.reference +
          ")\n",
      });
      setDraft(current);
    } catch (e) {
      setError(String(e));
    } finally {
      setPreparing(false);
    }
  };
  const retryPending = async () => {
    const op = pending.current;
    if (!op) return;
    try {
      const result = await write(op.name, op.arguments, true);
      if (typeof result.body === "string") {
        if (op.name === "create_document_draft") showDraft(result);
        else {
          // The replay is a snapshot of the original request. Keep text entered
          // after the lost response and advance only the saved base and ETag.
          setDraft(result);
          setStatus("下書き保存済み");
        }
      } else if (result.source_revision) {
        if (draft) setDraft({ ...draft, state: "committed" });
        setStatus("保存版確定済み");
        setSavedSource(
          await call("get_document", { source_id: result.source_id }),
        );
        if (draft)
          setDraft(
            await call("get_document_draft", { draft_id: draft.draft_id }),
          );
      } else if (result.reference && draft) {
        setDraft({
          ...draft,
          etag: result.etag,
          version: draft.version + 1,
          attachments: [...draft.attachments, result.attachment_id],
        });
        const filename = String(op.arguments.filename)
          .replaceAll("[", "")
          .replaceAll("]", "");
        setBody(
          (local) =>
            local + "\n![" + filename + "](" + result.reference + ")\n",
        );
        setGeneration((v) => v + 1);
        setStatus("下書き保存済み");
      }
    } catch {
      /* the original operation remains available for retry */
    }
  };
  const draftId = draft?.draft_id;
  const getAttachment = useCallback(
    (attachment_id: string) =>
      call("get_draft_attachment", { draft_id: draftId, attachment_id }),
    [call, draftId],
  );
  if (!session)
    return (
      <main>
        <h1>外部文書の編集</h1>
        <p>接続コードを認証済みのチャットまたはホストから承認してください。</p>
        <output className="connection-code">{code}</output>
        <p>接続したチャットで、このコードの編集接続を承認してください。</p>
        <p role="alert">{error}</p>
      </main>
    );
  return (
    <main>
      <header>
        <h1>外部文書の編集</h1>
        <p>人とAIで同じ下書きを編集できます。</p>
      </header>
      <p role="status">
        {status}
        {dirty ? " · 未保存の変更あり" : ""}
      </p>
      {expired && (
        <p role="alert">
          接続が失効したため、編集内容は保存されていません。画面の内容は読み取り専用で保持しています。
          下の「退避した入力」からコピーまたは保存してから、再接続してください。
        </p>
      )}
      {error && (
        <aside role="alert">
          <p>{error}</p>
          <button
            onClick={() => void retryPending()}
            disabled={working || !pending.current}
          >
            同じ操作で再試行
          </button>
          <button onClick={() => void refresh()} disabled={working || !draft}>
            入力を退避して最新下書きを取得
          </button>
          <button onClick={() => location.reload()}>再接続</button>
        </aside>
      )}
      {!draft ? (
        <section>
          <label>
            タイトル
            <input
              aria-label="タイトル"
              value={title}
              disabled={working || !!pending.current}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <button
            disabled={working || !!pending.current}
            onClick={() => void create()}
          >
            新しい下書き
          </button>
          <ul>
            {documents.map((d) => (
              <li key={d.source_id}>
                <button
                  disabled={
                    working ||
                    !!pending.current ||
                    d.content_kind !== "original_markdown"
                  }
                  onClick={() => void create(d)}
                >
                  {d.metadata.title}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <>
          <label>
            タイトル
            <input
              aria-label="タイトル"
              value={title}
              disabled={
                working || awaitingCommit || draft.state === "committed"
              }
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <nav>
            <button
              onClick={() => {
                setMode("rich");
                setGeneration((v) => v + 1);
              }}
            >
              見たまま編集
            </button>
            <button onClick={() => setMode("source")}>ソース編集</button>
            <button
              disabled={
                working || !!pending.current || draft.state === "committed"
              }
              onClick={() => void save().catch(() => {})}
            >
              下書きを保存
            </button>
            <button
              disabled={
                working || !!pending.current || draft.state === "committed"
              }
              onClick={() => void commit()}
            >
              保存版を確定
            </button>
          </nav>
          {mode === "rich" ? (
            <RawEditor
              key={generation}
              initial={body}
              readOnly={
                working || awaitingCommit || draft.state === "committed"
              }
              onChange={setBody}
              getAttachment={getAttachment}
            />
          ) : (
            <textarea
              aria-label="Markdownソース"
              value={body.replace(/\r\n/g, "\n")}
              disabled={
                working || awaitingCommit || draft.state === "committed"
              }
              onChange={(e) =>
                setBody((previous) => sourceEdit(previous, e.target.value))
              }
            />
          )}
          <label>
            添付ファイル
            <input
              type="file"
              disabled={
                working || !!pending.current || draft.state === "committed"
              }
              onChange={(e) => {
                if (e.target.files?.[0]) void attach(e.target.files[0]);
              }}
            />
          </label>
          <small>
            下書き {draft.draft_id} · 版 {draft.version} · 基準{" "}
            {draft.base_revision ?? "新規"}
          </small>
          {savedSource && (
            <p>
              文書 {savedSource.source_id} · 保存版{" "}
              {savedSource.source_revision}
            </p>
          )}
        </>
      )}
      {backup && (
        <details open>
          <summary>退避した入力</summary>
          <textarea aria-label="退避した入力" value={backup} readOnly />
          <p>
            <button onClick={() => void copyBackup()}>コピー</button>
            <button onClick={downloadBackup}>Markdownとして保存</button>
          </p>
        </details>
      )}
    </main>
  );
}
const root = document.getElementById("root");
if (root)
  createRoot(root).render(
    <AppErrorBoundary>
      <Editor />
    </AppErrorBoundary>,
  );
