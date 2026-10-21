import { useRef, useState, type DragEvent, type HTMLAttributes } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorText, post } from "../../lib/api";
import { queryKeys } from "../../lib/query-keys";
import { itemKey, itemName, type Item, type ItemRef } from "../../lib/types";
import {
  COPY_REFUSED,
  OPERATION_DONE,
  toRefs,
  type Operation,
  type OperationInput,
  type Pending,
} from "./operations";
import type { OperationLock } from "./useOperationLock";

interface Request {
  action: string;
  items?: ItemRef[];
  name?: string;
  destination?: string | null;
  parent?: string | null;
}
/**
 * Folder and file operations (create, rename, move, copy, delete), started
 * from the toolbar, the item menu or a drop. The dialog's uncommitted input is
 * not held here; the dialog hands it over in `submit`.
 */
export function useLibraryOperations({
  folder,
  selected,
  holdsDiagram,
  lock,
  setMessage,
  onFailure,
  clearSelection,
}: {
  folder: string | null;
  selected: Item[];
  holdsDiagram: (item: Item) => boolean;
  lock: OperationLock;
  setMessage: (text: string) => void;
  /** Called with the text of every failed operation, wherever it is shown. */
  onFailure?: (text: string) => void;
  clearSelection: () => void;
}) {
  const cache = useQueryClient(),
    [pending, setPending] = useState<Pending | null>(null),
    dragged = useRef<ItemRef[] | null>(null);
  async function execute(
    request: Request,
    success: string,
    onError: (text: string) => void,
    onSuccess?: () => void,
  ) {
    if (!lock.acquire()) return;
    try {
      await post(
        request.action === "create"
          ? "/api/folders"
          : "/api/library/operations",
        request.action === "create"
          ? { name: request.name, parent_id: request.parent }
          : request,
      );
      clearSelection();
      onSuccess?.();
      setMessage(success);
    } catch (e) {
      onError(errorText(e));
      onFailure?.(errorText(e));
    } finally {
      await cache.invalidateQueries({ queryKey: queryKeys.library });
      lock.release();
    }
  }
  /**
   * Delete runs at once; there is no dialog (the message reports the result).
   * The message names what was deleted, because the selection is gone by then.
   */
  function remove(targets: Item[]) {
    return execute(
      { action: "delete", items: toRefs(targets) },
      targets.length === 1
        ? `「${itemName(targets[0])}」を削除しました。`
        : `${targets.length}件を削除しました。`,
      setMessage,
    );
  }
  function start(action: Operation | "delete", targets = selected) {
    if (lock.isBusy() || (!targets.length && action !== "create")) return;
    if (action === "copy" && targets.some(holdsDiagram)) {
      setMessage(COPY_REFUSED);
      return;
    }
    if (action === "delete") {
      void remove(targets);
      return;
    }
    setPending({ action, items: targets, parent: folder });
  }
  /** Dialog confirmation. A failure goes back to the dialog, which keeps its input. */
  function submit(input: OperationInput, onError: (text: string) => void) {
    return execute(
      {
        action: input.action,
        items: toRefs(input.items),
        name: input.name,
        destination: input.destination,
        parent: input.parent,
      },
      OPERATION_DONE[input.action],
      onError,
      () => setPending(null),
    );
  }
  function dropProps(id: string | null): HTMLAttributes<HTMLElement> {
    return {
      onDragOver: (e) => {
        if (dragged.current && !lock.busy) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          e.currentTarget.classList.add("drop-target");
        }
      },
      onDragLeave: (e) => e.currentTarget.classList.remove("drop-target"),
      onDrop: (e) => {
        if (e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.classList.remove("drop-target");
        const targets = dragged.current;
        dragged.current = null;
        if (targets && !lock.busy)
          void execute(
            { action: "move", items: targets, destination: id },
            OPERATION_DONE.move,
            setMessage,
          );
      },
    };
  }
  return {
    pending,
    start,
    submit,
    dropProps,
    cancel: () => setPending(null),
    /** Dragging a checked item moves the whole selection. */
    beginDrag(item: Item, e: DragEvent) {
      if (lock.busy) {
        e.preventDefault();
        return;
      }
      dragged.current = selected.some((s) => itemKey(s) === itemKey(item))
        ? toRefs(selected)
        : toRefs([item]);
      e.dataTransfer.setData("text/plain", itemName(item));
      e.dataTransfer.effectAllowed = "move";
    },
    endDrag() {
      dragged.current = null;
      document
        .querySelectorAll(".drop-target")
        .forEach((n) => n.classList.remove("drop-target"));
    },
  };
}
