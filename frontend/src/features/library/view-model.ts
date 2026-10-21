import type { HTMLAttributes, DragEvent } from "react";
import type { Folder, Item } from "../../lib/types";
import type { Scope } from "./entries";
import type { Operation } from "./operations";

/**
 * What the list and the gallery share: the same items, the same selection and
 * the same operations. Neither view keeps state of its own, so switching
 * between them cannot change the targets, the selection or an open dialog.
 */
export interface ItemViewModel {
  folders: Folder[];
  scope: Scope;
  /** Items shown (after search, type and sort). */
  items: Item[];
  selected: Item[];
  busy: boolean;
  isChecked(item: Item): boolean;
  toggle(item: Item, value: boolean): void;
  selectAll(): void;
  clearSelection(): void;
  open(item: Item): void;
  start(action: Operation | "delete", targets?: Item[]): void;
  holdsDiagram(item: Item): boolean;
  dropProps(id: string | null): HTMLAttributes<HTMLElement>;
  beginDrag(item: Item, e: DragEvent): void;
  endDrag(): void;
}
