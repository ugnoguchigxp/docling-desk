import { itemName, type Item, type ItemRef } from "../../lib/types";

export type Operation = "create" | "rename" | "move" | "copy";
/** A dialog operation waiting for the user's input. */
export interface Pending {
  action: Operation;
  items: Item[];
  parent: string | null;
}
/** What the dialog sends once the input is confirmed. */
export interface OperationInput extends Pending {
  name: string;
  destination: string | null;
}
export const COPY_REFUSED = "図表の複製には対応していません。";
export const OPERATION_TITLES: Record<Operation, string> = {
  create: "新しいフォルダー",
  rename: "名前を変更",
  move: "移動先を選択",
  copy: "コピー先を選択",
};
export const OPERATION_SUBMIT: Record<Operation, string> = {
  create: "作成",
  rename: "名前を変更",
  move: "ここに移動",
  copy: "ここにコピー",
};
export const OPERATION_DONE: Record<Operation, string> = {
  create: "フォルダーを作成しました。",
  rename: "名前を変更しました。",
  move: "移動しました。",
  copy: "コピーしました。",
};
export const isNaming = (action: Operation) =>
  action === "create" || action === "rename";
export const toRefs = (items: ItemRef[]): ItemRef[] =>
  items.map(({ kind, id }) => ({ kind, id }));
export const initialName = (pending: Pending) =>
  pending.action === "rename" ? itemName(pending.items[0]) : "";
