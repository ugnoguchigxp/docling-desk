import type { HTMLAttributes } from "react";
import { Breadcrumbs, Button, SelectField } from "../../components/ui";
import type { Folder } from "../../lib/types";
import type { Layout, Scope } from "./entries";
import { chain, crumbs } from "./folders";

/** Where the list is (breadcrumbs or image folder filter) and how it is shown. */
export function LocationBar({
  folders,
  folder,
  scope,
  layout,
  onFolder,
  onScope,
  onLayout,
  dropProps,
}: {
  folders: Folder[];
  folder: string | null;
  scope: Scope;
  layout: Layout;
  onFolder: (id: string | null) => void;
  onScope: (scope: Scope) => void;
  onLayout: (layout: Layout) => void;
  dropProps: (id: string | null) => HTMLAttributes<HTMLElement>;
}) {
  return (
    <div className="library-location-bar">
      {scope === "images" ? (
        <label className="image-folder-filter">
          保存先
          <SelectField
            aria-label="画像の保存先"
            value={folder || ""}
            onChange={(e) => onFolder(e.target.value || null)}
          >
            <option value="">すべてのフォルダー</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {chain(folders, f.id)
                  .map((p) => p.name)
                  .join(" / ")}
                （配下を含む）
              </option>
            ))}
          </SelectField>
        </label>
      ) : (
        <Breadcrumbs
          id="breadcrumbs"
          aria-label="現在のフォルダー"
          items={crumbs(folders, folder)}
          onSelect={onFolder}
          dropProps={(id) => dropProps(id)}
        />
      )}
      <div className="library-view-controls">
        <div
          className="library-view-bar"
          role="group"
          aria-label="資料の表示対象"
        >
          <Button
            aria-pressed={scope === "folder"}
            onClick={() => onScope("folder")}
          >
            すべての資料
          </Button>
          <Button
            aria-pressed={scope === "images"}
            onClick={() => onScope("images")}
          >
            画像を探す
          </Button>
        </div>
        <div
          role="group"
          aria-label="一覧の表示形式"
          className="library-layout-toggle"
        >
          <Button
            aria-pressed={layout === "list"}
            onClick={() => onLayout("list")}
          >
            リスト
          </Button>
          <Button
            aria-pressed={layout === "gallery"}
            onClick={() => onLayout("gallery")}
          >
            ギャラリー
          </Button>
        </div>
      </div>
    </div>
  );
}
