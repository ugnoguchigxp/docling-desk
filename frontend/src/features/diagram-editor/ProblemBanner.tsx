import { Button } from "../../components/ui";
import { when, type Problem } from "./problems";
import type { Stash } from "./stash";

/** A save/restore/draft problem with the choices for dealing with it. */
export function ProblemBanner({
  problem,
  acknowledged,
  busy,
  onRetry,
  onReload,
  onReloadLatest,
  onAcknowledge,
  onSaveCopy,
}: {
  problem: Problem;
  acknowledged: boolean;
  busy: boolean;
  onRetry: (action: "save" | "restore") => void;
  onReload: () => void;
  onReloadLatest: () => void;
  onAcknowledge: () => void;
  onSaveCopy: () => void;
}) {
  return (
    <div
      role="alert"
      className="diagram-editor-problem"
      data-kind={problem.kind}
    >
      <p>{problem.message}</p>
      {!acknowledged && (
        <div className="diagram-editor-options">
          {problem.retry && (
            <Button
              onClick={() => onRetry(problem.action ?? "save")}
              disabled={busy}
            >
              再試行
            </Button>
          )}
          {problem.reload && (
            <Button onClick={onReload} disabled={busy}>
              内容を再読み込み
            </Button>
          )}
          {problem.kind === "conflict" ? (
            <>
              <Button
                className="danger"
                onClick={onReloadLatest}
                disabled={busy}
              >
                最新版を読み込む（編集中の内容は破棄）
              </Button>
              <Button onClick={onAcknowledge}>このまま編集を続ける</Button>
              <Button onClick={onSaveCopy}>
                編集中の内容を.excalidrawで書き出す
              </Button>
            </>
          ) : (
            <Button onClick={onAcknowledge}>閉じる</Button>
          )}
        </div>
      )}
    </div>
  );
}

/** Offer to reopen input that was left unsaved when the editor was closed. */
export function StashBanner({
  stash,
  onOpen,
  onDrop,
}: {
  stash: Stash;
  onOpen: () => void;
  onDrop: () => void;
}) {
  return (
    <div role="alert" className="diagram-editor-problem" data-kind="rescue">
      <p>
        前回、保存されないまま閉じた編集内容があります（{when(stash.saved)}）。
      </p>
      <div className="diagram-editor-options">
        <Button onClick={onOpen}>その内容を開く</Button>
        <Button className="danger" onClick={onDrop}>
          破棄する
        </Button>
      </div>
    </div>
  );
}
