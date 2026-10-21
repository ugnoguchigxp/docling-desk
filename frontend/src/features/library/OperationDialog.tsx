import { useState } from "react";
import { Icon } from "../../components/Icons";
import {
  Breadcrumbs,
  Button,
  Dialog,
  DialogActions,
  DialogHeading,
  StatusMessage,
  TextField,
} from "../../components/ui";
import { itemName, type Folder } from "../../lib/types";
import { crumbs, folderPath, movesIntoItself } from "./folders";
import {
  initialName,
  isNaming,
  OPERATION_SUBMIT,
  OPERATION_TITLES,
  type OperationInput,
  type Pending,
} from "./operations";

/**
 * The dialog for create, rename, move and copy. The name, the destination and
 * the error are this dialog's own: they start from `pending` each time it opens
 * and survive a failed submit, so the user can fix the input and retry.
 */
export function OperationDialog({
  pending,
  folders,
  busy,
  onSubmit,
  onClose,
}: {
  pending: Pending | null;
  folders: Folder[];
  busy: boolean;
  onSubmit: (input: OperationInput, onError: (text: string) => void) => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      id="libraryDialog"
      className="library-dialog"
      aria-labelledby="libraryDialogTitle"
      open={!!pending}
      busy={busy}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {pending && (
        <OperationForm
          pending={pending}
          folders={folders}
          busy={busy}
          onSubmit={onSubmit}
          onClose={onClose}
        />
      )}
    </Dialog>
  );
}
function OperationForm({
  pending,
  folders,
  busy,
  onSubmit,
  onClose,
}: {
  pending: Pending;
  folders: Folder[];
  busy: boolean;
  onSubmit: (input: OperationInput, onError: (text: string) => void) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(() => initialName(pending)),
    [destination, setDestination] = useState(pending.parent),
    [error, setError] = useState(""),
    naming = isNaming(pending.action),
    excluded = (id: string | null) =>
      movesIntoItself(folders, pending.items, id);
  return (
    <form
      id="libraryForm"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        onSubmit({ ...pending, name, destination }, setError);
      }}
    >
      <DialogHeading
        id="libraryDialogTitle"
        title={OPERATION_TITLES[pending.action]}
        onClose={onClose}
        disabled={busy}
      />
      <p id="operationDescription">
        {pending.items.length
          ? pending.items.length === 1
            ? itemName(pending.items[0])
            : `${pending.items.length}件の項目`
          : `作成先：${folderPath(folders, pending.parent)}`}
      </p>
      <label id="operationNameLabel" hidden={!naming}>
        名前
        <TextField
          id="operationName"
          type="text"
          maxLength={200}
          autoComplete="off"
          required={naming}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <div id="destinationBrowser" hidden={naming}>
        <Breadcrumbs
          id="destinationBreadcrumbs"
          aria-label="移動先のフォルダー"
          items={crumbs(folders, destination)}
          onSelect={setDestination}
        />
        <div id="destinationFolders">
          {folders
            .filter((f) => f.parent_id === destination)
            .sort((a, b) => a.name.localeCompare(b.name, "ja"))
            .map((f) => (
              <Button
                key={f.id}
                className="destination-folder"
                disabled={busy || excluded(f.id)}
                onClick={() => setDestination(f.id)}
              >
                <Icon name="folder" />
                {f.name}
                <Icon name="next" />
              </Button>
            ))}
        </div>
        <p id="destinationHint">
          {folders.some((f) => f.parent_id === destination)
            ? "フォルダーを開いて保存先を選んでください。"
            : "このフォルダーに下位フォルダーはありません。"}
        </p>
      </div>
      <StatusMessage id="operationError" kind="error" hidden={!error}>
        {error}
      </StatusMessage>
      <DialogActions>
        <Button id="cancelOperation" onClick={onClose} disabled={busy}>
          キャンセル
        </Button>
        <Button
          id="submitOperation"
          type="submit"
          className="primary"
          disabled={busy || (!naming && excluded(destination))}
        >
          {OPERATION_SUBMIT[pending.action]}
        </Button>
      </DialogActions>
    </form>
  );
}
