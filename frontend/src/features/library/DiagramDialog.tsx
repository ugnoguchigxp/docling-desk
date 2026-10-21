import { useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogHeading,
  StatusMessage,
  TextField,
} from "../../components/ui";
import type { Folder } from "../../lib/types";
import { DEFAULT_DIAGRAM_NAME } from "./diagram";
import { folderPath } from "./folders";

/**
 * The dialog that names a new diagram. The name and the error are this
 * dialog's own and survive a failed submit; a retry reuses the same attempt.
 */
export function DiagramDialog({
  target,
  folders,
  busy,
  onSubmit,
  onClose,
}: {
  target: { parent: string | null } | null;
  folders: Folder[];
  busy: boolean;
  onSubmit: (name: string, onError: (text: string) => void) => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      id="diagramDialog"
      className="library-dialog"
      aria-labelledby="diagramDialogTitle"
      open={!!target}
      busy={busy}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {target && (
        <DiagramForm
          parent={target.parent}
          folders={folders}
          busy={busy}
          onSubmit={onSubmit}
          onClose={onClose}
        />
      )}
    </Dialog>
  );
}
function DiagramForm({
  parent,
  folders,
  busy,
  onSubmit,
  onClose,
}: {
  parent: string | null;
  folders: Folder[];
  busy: boolean;
  onSubmit: (name: string, onError: (text: string) => void) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(DEFAULT_DIAGRAM_NAME),
    [error, setError] = useState("");
  return (
    <form
      id="diagramForm"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        onSubmit(name, setError);
      }}
    >
      <DialogHeading
        id="diagramDialogTitle"
        title="図表を作成"
        onClose={onClose}
        disabled={busy}
      />
      <p id="diagramLocation">{`作成先：${folderPath(folders, parent)}`}</p>
      <label>
        名前
        <TextField
          id="diagramName"
          type="text"
          maxLength={200}
          autoComplete="off"
          required
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <p className="upload-hint">拡張子「.excalidraw」は自動で付きます。</p>
      <StatusMessage id="diagramError" kind="error" hidden={!error}>
        {error}
      </StatusMessage>
      <DialogActions>
        <Button id="cancelDiagram" onClick={onClose} disabled={busy}>
          キャンセル
        </Button>
        <Button
          id="submitDiagram"
          type="submit"
          className="primary"
          disabled={busy}
        >
          作成
        </Button>
      </DialogActions>
    </form>
  );
}
