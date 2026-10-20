import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ActionLink, Button, StatusMessage } from "../components/ui";
import { withBase } from "../lib/base";
import { errorText } from "../lib/api";
import {
  DiagramRequestError,
  SCENE_TYPE,
  diagramContentUrl,
  getContent,
  getDiagram,
  getDraftContent,
  type DiagramInfo,
} from "../lib/diagrams";
import type { Job } from "../lib/types";
import { HistoryPanel } from "./diagram-editor/HistoryPanel";
import { ProblemBanner, StashBanner } from "./diagram-editor/ProblemBanner";
import { when, type Problem } from "./diagram-editor/problems";
import { newSession, type DraftState, type Ui } from "./diagram-editor/session";
import { clearStash, readStash, type Stash } from "./diagram-editor/stash";
import { useDiagramSave } from "./diagram-editor/useDiagramSave";
import { discardOwnDraft, useDraftSync } from "./diagram-editor/useDraftSync";
import "./diagram-editor.css";

const DiagramCanvas = lazy(() => import("../diagram/DiagramCanvas"));

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function DiagramEditor({
  job,
  onChanged,
}: {
  job: Job;
  onChanged?: () => void;
}) {
  const id = job.id;
  const s = useMemo(() => newSession(id), [id]);
  const [phase, setPhase] = useState<"loading" | "ready" | "failed">("loading");
  const [loadError, setLoadError] = useState("");
  const [info, setInfo] = useState<DiagramInfo | null>(null);
  const [initialScene, setInitialScene] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [hasDraft, setHasDraft] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "restore" | "export">("");
  const [problem, setProblem] = useState<Problem | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [draftNote, setDraftNote] = useState("");
  const [notice, setNotice] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const [needsReload, setNeedsReload] = useState(false);
  const [rescue, setRescue] = useState<Stash | null>(null);

  const changedRef = useRef(onChanged);
  changedRef.current = onChanged;

  // Everything the hooks show goes through here and is dropped after unmount.
  const ui = useMemo<Ui>(() => {
    const live =
      <A extends unknown[]>(fn: (...args: A) => void) =>
      (...args: A) => {
        if (!s.unmounted) fn(...args);
      };
    return {
      raise: live((value: Problem | null) => {
        s.problem = value;
        setProblem(value);
        setAcknowledged(false);
      }),
      notice: live(setNotice),
      draftNote: live(setDraftNote),
      hasDraft: live(setHasDraft),
      dirty: live(setDirty),
      info: live(setInfo),
      busy: live(setBusy),
      history: live(() => setHistoryKey((n) => n + 1)),
      needsReload: live(setNeedsReload),
      changed: live(() => changedRef.current?.()),
    };
  }, [s]);

  /** Open `scene` in a fresh canvas and reset all editing bookkeeping. */
  const adopt = useCallback(
    (
      next: DiagramInfo,
      scene: string,
      draft: DraftState | null = null,
      resumed = false,
      fromStash = false,
    ) => {
      window.clearTimeout(s.timer);
      s.info = next;
      s.draft = draft;
      s.current = null;
      s.opened = scene;
      s.baseline = null;
      s.unsaved = resumed || fromStash;
      s.rescued = fromStash;
      s.seenFirst = false;
      s.lastDraft = null;
      s.lastDraftAt = 0;
      s.attempts = [];
      s.halted = null;
      s.pending = null;
      s.restoring = null;
      s.paused = false;
      s.generation += 1;
      setInfo(next);
      setInitialScene(scene);
      setGeneration(s.generation);
      setDirty(resumed || fromStash);
      setHasDraft(draft != null);
      setNeedsReload(false);
      setDraftNote(
        fromStash
          ? "保存されなかった編集内容を開きました"
          : resumed && next.draft
            ? `保存していなかった下書きを開きました（${when(next.draft.updated)}）`
            : "",
      );
      setHistoryKey((n) => n + 1);
      setPhase("ready");
    },
    [s],
  );

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setPhase("loading");
      setLoadError("");
      try {
        const { info: fresh, etag } = await getDiagram(id, signal);
        let draft: DraftState | null = null;
        let scene: string | null = null;
        if (fresh.draft) {
          try {
            const content = await getDraftContent(id, fresh.draft.id, signal);
            scene = content.scene;
            draft = {
              id: fresh.draft.id,
              etag: content.etag || fresh.draft.etag,
            };
          } catch (error) {
            if (!(error instanceof DiagramRequestError) || error.status !== 404)
              throw error;
          }
        }
        scene ??= (await getContent(id, fresh.revision_id, signal)).scene;
        if (signal?.aborted) return;
        ui.raise(null);
        adopt(
          { ...fresh, etag: etag || fresh.etag },
          scene,
          draft,
          draft != null,
        );
        if (
          draft &&
          fresh.draft &&
          fresh.draft.base_revision !== fresh.revision_id
        )
          ui.raise({
            kind: "conflict",
            message:
              "この下書きは古い版を基にしているため、最新版として保存できません。編集中の内容はこの画面に残っています。",
            retry: false,
          });
        const stash = readStash(id);
        if (stash && stash.scene !== scene) setRescue(stash);
        else {
          setRescue(null);
          if (stash) clearStash(id);
        }
      } catch (error) {
        if (signal?.aborted) return;
        s.paused = false;
        if (s.info) {
          // The editor is open on older content: say so instead of failing
          // silently, and keep what is being edited.
          setPhase("ready");
          ui.raise({
            kind: "failure",
            message: `最新の内容を読み込めませんでした。${errorText(error)} 編集中の内容はこの画面に残っています。`,
            retry: false,
            reload: true,
          });
        } else {
          setLoadError(errorText(error));
          setPhase("failed");
        }
      }
    },
    [id, s, ui, adopt],
  );

  useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal);
    return () => abort.abort();
  }, [load]);

  const { uploadDraft, schedule, onSceneChange } = useDraftSync(s, ui);
  const adoptFresh = useCallback(
    (next: DiagramInfo, scene: string) => adopt(next, scene),
    [adopt],
  );
  const { save, restore } = useDiagramSave(s, ui, {
    adopt: adoptFresh,
    uploadDraft,
    schedule,
  });

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const reloadLatest = useCallback(async () => {
    s.paused = true;
    window.clearTimeout(s.timer);
    // Let an upload that is on its way finish so it cannot resurrect the draft.
    await s.flight;
    window.clearTimeout(s.timer);
    if (s.draft) {
      try {
        await discardOwnDraft(id, s.draft);
      } catch (error) {
        // Loading now would reopen that same draft: stop and say so.
        s.paused = false;
        ui.raise({
          kind: "failure",
          message: `下書きを破棄できませんでした。${errorText(error)} 編集中の内容はこの画面に残っています。`,
          retry: false,
        });
        return;
      }
    }
    s.draft = null;
    clearStash(id);
    ui.raise(null);
    await load();
  }, [id, s, ui, load]);

  const discard = () => {
    if (
      !window.confirm(
        "下書きを破棄して、最新の保存版を開きます。よろしいですか？",
      )
    )
      return;
    void reloadLatest();
  };

  const openStash = () => {
    const kept = rescue;
    const base = s.info;
    if (!kept || !base) return;
    setRescue(null);
    clearStash(id);
    adopt(base, kept.scene, s.draft, false, true);
    if (kept.base && kept.base !== base.revision_id)
      setNotice(
        "この内容は古い版を基にしています。保存すると、その後の変更は新しい版で置き換わります（以前の版は履歴から戻せます）。",
      );
  };
  const dropStash = () => {
    clearStash(id);
    setRescue(null);
  };

  const stem = (info?.name ?? job.filename).replace(/\.excalidraw$/i, "");
  const exportAs = async (kind: "svg" | "webp" | "jpeg" | "html") => {
    const scene = s.current ?? s.opened;
    if (scene == null || busy) return;
    setBusy("export");
    setNotice("");
    try {
      const exporter = await import("../diagram/export");
      if (kind === "svg")
        download(await exporter.exportSvg(scene), `${stem}.svg`);
      else if (kind === "html")
        download(await exporter.exportHtml(scene, stem), `${stem}.html`);
      else {
        const { blob, format } = await exporter.exportRaster(scene, kind);
        download(blob, `${stem}.${format === "jpeg" ? "jpg" : "webp"}`);
        if (format !== kind)
          setNotice(
            "このブラウザーはWebPに対応していないため、JPEGで書き出しました。",
          );
      }
    } catch (error) {
      setNotice(`書き出せませんでした。${errorText(error)}`);
    } finally {
      setBusy("");
    }
  };
  const saveCopy = () => {
    const scene = s.current ?? s.opened;
    if (scene != null)
      download(new Blob([scene], { type: SCENE_TYPE }), `${stem}.excalidraw`);
  };

  if (phase === "loading" && !info)
    return (
      <section className="diagram-editor" aria-label="図表エディター">
        <StatusMessage className="diagram-editor-state">
          図表を読み込み中…
        </StatusMessage>
      </section>
    );
  if (phase === "failed" && !info)
    return (
      <section className="diagram-editor" aria-label="図表エディター">
        <div role="alert" className="diagram-editor-state">
          <p>図表を開けませんでした。{loadError}</p>
          <Button onClick={() => void load()}>再読み込み</Button>
        </div>
      </section>
    );

  const status =
    busy === "save"
      ? "保存中…"
      : busy === "restore"
        ? "版を復元中…"
        : problem
          ? "保存できていません"
          : dirty
            ? "未保存の変更があります"
            : "保存済み";
  const exports = [
    ["svg", "SVG"],
    ["webp", "WebP"],
    ["jpeg", "JPEG"],
    ["html", "HTML"],
  ] as const;

  return (
    <section
      className="diagram-editor"
      aria-label="図表エディター"
      aria-busy={phase === "loading"}
    >
      <header className="diagram-editor-bar">
        <strong className="diagram-editor-name" title={info?.name}>
          {info?.name ?? job.filename}
        </strong>
        <span className="diagram-editor-status" role="status">
          {status}
          {info && ` · 版 ${info.revision_number}`}
        </span>
        {draftNote && (
          <small className="diagram-editor-draft">{draftNote}</small>
        )}
        <div className="diagram-editor-actions">
          <Button
            className="primary"
            onClick={() => void save()}
            disabled={!!busy || needsReload || (!dirty && !problem)}
          >
            保存
          </Button>
          {hasDraft && (
            <Button onClick={discard} disabled={!!busy}>
              下書きを破棄
            </Button>
          )}
          <Button
            aria-expanded={historyOpen}
            aria-controls="diagramHistory"
            onClick={() => setHistoryOpen((open) => !open)}
          >
            版の履歴
          </Button>
          <span
            role="group"
            aria-label="書き出し"
            className="diagram-editor-exports"
          >
            {exports.map(([kind, label]) => (
              <Button
                key={kind}
                onClick={() => void exportAs(kind)}
                disabled={!!busy}
                aria-label={`${label}で書き出す`}
              >
                {label}
              </Button>
            ))}
          </span>
          <ActionLink
            href={withBase(diagramContentUrl(id, { download: true }))}
            id="originalDownload"
            className="diagram-editor-original"
          >
            原本を保存
          </ActionLink>
        </div>
      </header>
      {problem && (
        <ProblemBanner
          problem={problem}
          acknowledged={acknowledged}
          busy={!!busy}
          onRetry={(action) => {
            if (action === "restore") {
              if (s.lastRestore) void restore(s.lastRestore, true);
            } else void save();
          }}
          onReload={() => {
            ui.raise(null);
            void load();
          }}
          onReloadLatest={() => void reloadLatest()}
          onAcknowledge={() => setAcknowledged(true)}
          onSaveCopy={saveCopy}
        />
      )}
      {rescue && (
        <StashBanner stash={rescue} onOpen={openStash} onDrop={dropStash} />
      )}
      {notice && (
        <StatusMessage className="diagram-editor-notice">
          {notice}
        </StatusMessage>
      )}
      <div className="diagram-editor-body">
        <div className="diagram-editor-canvas">
          <Suspense
            fallback={
              <StatusMessage className="diagram-editor-state">
                図表エディターを読み込み中…
              </StatusMessage>
            }
          >
            <DiagramCanvas
              key={generation}
              initialScene={initialScene}
              // A canvas that was replaced may still flush a last change.
              onChange={(scene) => {
                if (generation === s.generation) onSceneChange(scene);
              }}
              onError={(message) =>
                ui.raise({ kind: "failure", message, retry: false })
              }
              onNotice={setNotice}
            />
          </Suspense>
        </div>
        {historyOpen && (
          <HistoryPanel
            id={id}
            refreshKey={historyKey}
            busy={!!busy}
            onRestore={(revision) => void restore(revision)}
          />
        )}
      </div>
    </section>
  );
}
