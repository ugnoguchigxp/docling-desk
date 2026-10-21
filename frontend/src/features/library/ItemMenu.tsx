import { ActionLink, Button, DisclosureMenu } from "../../components/ui";
import { originalUrl } from "../../lib/api";
import { withBase } from "../../lib/base";
import { itemName, type Item } from "../../lib/types";
import { COPY_REFUSED } from "./operations";
import type { ItemViewModel } from "./view-model";

/** The "…" menu of one item; list rows and gallery cards use the same one. */
export function ItemMenu({
  item,
  model,
}: {
  item: Item;
  model: ItemViewModel;
}) {
  const refused = model.holdsDiagram(item);
  return (
    <DisclosureMenu
      className="item-menu"
      label={`${itemName(item)}の操作`}
      icon="more"
    >
      <Button onClick={() => model.open(item)}>開く</Button>
      <Button onClick={() => model.start("rename", [item])}>名前を変更</Button>
      <Button onClick={() => model.start("move", [item])}>移動先</Button>
      <Button
        disabled={refused}
        title={refused ? COPY_REFUSED : undefined}
        onClick={() => model.start("copy", [item])}
      >
        コピー先
      </Button>
      {item.kind === "file" && (
        <ActionLink href={withBase(originalUrl(item))}>原本を保存</ActionLink>
      )}
      <Button className="danger" onClick={() => model.start("delete", [item])}>
        削除
      </Button>
    </DisclosureMenu>
  );
}
