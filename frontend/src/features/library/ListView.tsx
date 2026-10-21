import { Icon } from "../../components/Icons";
import { Button, CheckboxField } from "../../components/ui";
import { isDocument, originalName } from "../../lib/formats";
import { isDone, itemKey, itemName, labels, type Item } from "../../lib/types";
import { kindLabel } from "./entries";
import { ItemMenu } from "./ItemMenu";
import type { ItemViewModel } from "./view-model";

const date = new Intl.DateTimeFormat("ja-JP", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
function stateLabel(item: Item) {
  if (item.kind === "folder") return "フォルダー";
  if (item.image) return "画像";
  if (item.diagram) return "閲覧可能";
  if (item.excalidraw) return "図表";
  return labels[item.state];
}
function pagesLabel(item: Item) {
  if (item.kind === "folder" || item.image || item.excalidraw) return "—";
  return isDocument(originalName(item)) && isDone(item)
    ? "文書全体"
    : item.pages || "—";
}
/** Opens the ".item-menu" of the row (right click), closing the others. */
function openRowMenu(row: HTMLElement) {
  document.querySelectorAll<HTMLDetailsElement>(".item-menu").forEach((n) => {
    n.open = false;
  });
  const n = row.querySelector<HTMLDetailsElement>(".item-menu");
  if (n) n.open = true;
}
export function ListView({ model }: { model: ItemViewModel }) {
  const { items, selected } = model;
  return (
    <table aria-label="保存済みの資料">
      <thead>
        <tr>
          <th scope="col" className="select-column">
            <CheckboxField
              id="selectAll"
              aria-label="表示中の項目をすべて選択"
              disabled={!items.length || model.busy}
              checked={!!items.length && selected.length === items.length}
              indeterminate={
                !!selected.length && selected.length !== items.length
              }
              onChange={(e) =>
                e.target.checked ? model.selectAll() : model.clearSelection()
              }
            />
          </th>
          <th scope="col" className="name-column">
            名前
          </th>
          <th scope="col" className="state-column">
            状態
          </th>
          <th scope="col" className="pages-column">
            ページ
          </th>
          <th scope="col" className="date-column">
            追加日時
          </th>
          <th scope="col" className="actions-column">
            <span className="sr-only">操作</span>
          </th>
        </tr>
      </thead>
      <tbody id="jobs">
        {items.map((item) => (
          <tr
            key={itemKey(item)}
            data-key={itemKey(item)}
            className={model.isChecked(item) ? "selected" : ""}
            {...(item.kind === "folder" ? model.dropProps(item.id) : {})}
            onClick={(e) => {
              if (model.busy) return;
              if (e.ctrlKey || e.metaKey || e.shiftKey)
                model.toggle(item, !model.isChecked(item));
              else model.open(item);
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              openRowMenu(e.currentTarget);
            }}
          >
            <td onClick={(e) => e.stopPropagation()}>
              <CheckboxField
                aria-label={`${itemName(item)}を選択`}
                checked={model.isChecked(item)}
                onChange={(e) => model.toggle(item, e.target.checked)}
                disabled={model.busy}
              />
            </td>
            <td>
              <Button
                className="file-open"
                aria-label={`${itemName(item)}を開く`}
                draggable
                onDragStart={(e) => model.beginDrag(item, e)}
                onDragEnd={model.endDrag}
              >
                {item.kind === "folder" ? (
                  <Icon name="folder" className="folder-kind" />
                ) : (
                  <span
                    className={`file-kind ${item.filename.split(".").pop()?.toLowerCase()}`}
                    aria-hidden="true"
                  >
                    {kindLabel(item, item.filename)}
                  </span>
                )}
                <span className="file-name" title={itemName(item)}>
                  {itemName(item)}
                </span>
              </Button>
            </td>
            <td>
              <span
                className={
                  item.kind === "file" ? `file-state ${item.state}` : ""
                }
              >
                {stateLabel(item)}
              </span>
              {item.kind === "file" &&
                Object.entries(item.translations || {}).map(([lang, c]) => (
                  <span className="translation-summary" key={lang}>
                    {lang === "en" ? "英訳" : "日本語訳"} {c.saved}/
                    {isDocument(originalName(item)) ? 1 : item.pages || "—"}
                    {c.active ? " · 翻訳中" : c.failed ? " · 再実行可" : ""}
                  </span>
                ))}
            </td>
            <td>{pagesLabel(item)}</td>
            <td title={new Date(item.created * 1000).toLocaleString("ja-JP")}>
              {date.format(new Date(item.created * 1000))}
            </td>
            <td>
              <ItemMenu item={item} model={model} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
