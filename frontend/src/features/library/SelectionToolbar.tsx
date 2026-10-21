import { Button, Toolbar } from "../../components/ui";
import { originalUrl } from "../../lib/api";
import { withBase } from "../../lib/base";
import { COPY_REFUSED } from "./operations";
import type { ItemViewModel } from "./view-model";

/** Operations on the checked items (the same model the item menus use). */
export function SelectionToolbar({ model }: { model: ItemViewModel }) {
  const { selected, busy } = model,
    copyBlocked = selected.some(model.holdsDiagram);
  return (
    <Toolbar
      id="selectionToolbar"
      className="selection-toolbar"
      hidden={!selected.length}
    >
      <span id="selectionCount">{selected.length}件選択</span>
      <Button
        id="selectionClear"
        disabled={busy}
        onClick={model.clearSelection}
      >
        選択解除
      </Button>
      <Button
        id="selectionRename"
        disabled={busy || selected.length !== 1}
        title={
          selected.length > 1 ? "名前を変更できるのは1件ずつです" : undefined
        }
        onClick={() => model.start("rename")}
      >
        名前を変更
      </Button>
      <Button
        id="selectionMove"
        disabled={busy}
        onClick={() => model.start("move")}
      >
        移動先
      </Button>
      <Button
        id="selectionCopy"
        disabled={busy || copyBlocked}
        title={copyBlocked ? COPY_REFUSED : undefined}
        onClick={() => model.start("copy")}
      >
        コピー先
      </Button>
      <Button
        id="selectionDownload"
        disabled={busy || selected.length !== 1 || selected[0]?.kind !== "file"}
        title={
          selected.length === 1 && selected[0]?.kind !== "file"
            ? "フォルダーは原本を保存できません"
            : selected.length > 1
              ? "原本を保存できるのは1件ずつです"
              : undefined
        }
        onClick={() => {
          const i = selected[0];
          if (i?.kind === "file") {
            const link = document.createElement("a");
            link.href = withBase(originalUrl(i));
            link.click();
          }
        }}
      >
        原本を保存
      </Button>
      <Button
        id="selectionDelete"
        className="danger"
        disabled={busy}
        title={`選択した${selected.length}件を削除します。確認なしですぐに実行され、元に戻せません。`}
        onClick={() => model.start("delete")}
      >
        削除
      </Button>
    </Toolbar>
  );
}
