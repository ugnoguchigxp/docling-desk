import { useState } from "react";
import { Icon } from "../../components/Icons";
import { Button, CheckboxField } from "../../components/ui";
import { originalName } from "../../lib/formats";
import { thumbnailUrl } from "../../lib/media";
import {
  itemKey,
  itemName,
  labels,
  type Item,
  type Job,
} from "../../lib/types";
import { kindLabel } from "./entries";
import { folderPath } from "./folders";
import { ItemMenu } from "./ItemMenu";
import type { ItemViewModel } from "./view-model";

function Thumbnail({ job }: { job: Job }) {
  const [failed, setFailed] = useState(false);
  const source = thumbnailUrl(job);
  return source && !failed ? (
    <img src={source} alt="" loading="lazy" onError={() => setFailed(true)} />
  ) : (
    <span className="gallery-file-kind">
      {kindLabel(job, originalName(job))}
    </span>
  );
}
function meta(item: Item) {
  if (item.kind === "folder") return "フォルダー";
  if (item.image)
    return `${item.image.width} × ${item.image.height} · ${item.image.media_type.split("/")[1].toUpperCase()}`;
  if (item.diagram) return "閲覧可能";
  if (item.excalidraw) return "図表";
  return labels[item.state];
}
export function GalleryView({ model }: { model: ItemViewModel }) {
  return (
    <ul className="library-gallery" aria-label="保存済みの資料">
      {model.items.map((item) => (
        <li
          key={itemKey(item)}
          className={model.isChecked(item) ? "selected" : ""}
          {...(item.kind === "folder" ? model.dropProps(item.id) : {})}
        >
          <div className="gallery-item-tools">
            <CheckboxField
              aria-label={`${itemName(item)}を選択`}
              checked={model.isChecked(item)}
              disabled={model.busy}
              onChange={(e) => model.toggle(item, e.target.checked)}
            />
            <ItemMenu item={item} model={model} />
          </div>
          <Button
            className="gallery-open"
            aria-label={`${itemName(item)}を開く`}
            onClick={() => model.open(item)}
            disabled={model.busy}
          >
            <span className="gallery-thumbnail">
              {item.kind === "folder" ? (
                <Icon name="folder" />
              ) : (
                <Thumbnail job={item} />
              )}
            </span>
            <span className="gallery-name">{itemName(item)}</span>
            <span className="gallery-meta">{meta(item)}</span>
            {model.scope === "images" && item.kind === "file" && (
              <span className="gallery-meta">
                {folderPath(model.folders, item.folder_id)}
              </span>
            )}
          </Button>
        </li>
      ))}
    </ul>
  );
}
